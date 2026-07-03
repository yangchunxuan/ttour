"""市场投放优化 agent 核心 —— Phase 6（V2 一·3：数据反馈 → 优化投放 闭环）。

闭环 = 投放 → 咨询 → 报价 → 成交 →【数据反馈 → 优化投放】。本 agent 负责最后两环：
读 funnel_events（客服/pipeline 已按 平台/地区/投放时间/广告素材 打标）做**全阶段**
转化统计（咨询→有效→报价→成单 + 成交金额 + 各段转化率），产出**可执行的预算调整
建议**（🟢加投 / ⚪维持 / 🟠收缩 / 🔴暂停），反馈给前端市场。

边界（§3 + 硬规矩「不划钱」）：本 agent **只产出建议，绝不真花广告费**。真去广告
平台调预算 = 需要你的广告账号 + 碰钱 = 人审的可插拔 AdSpendAdapter（协议留好，
真实现未接——那是要你广告账号的最后一公里）。
  - 有花费数据(spend_by_value) → 算 CPA/ROAS 排序（真优化依据）
  - 没有花费数据 → 按成交转化率排序（诚实降级，不假装有 ROI）

纯确定性统计，不依赖 SaleSmartly / VM / LLM。
"""

from __future__ import annotations

from typing import Optional, Protocol

from company.record.db import Database

STAGES = ["inquiry", "valid", "quoted", "won"]  # 咨询/有效/报价/成单
DIMENSIONS = ("platform", "region", "campaign_time", "ad_content")


def _rate(num: int, den: int) -> float:
    return round(num / den, 4) if den else 0.0


class AdSpendAdapter(Protocol):
    """投放平台适配器协议。真去调广告预算 = 碰钱 + 对外 = 人审。真实现未接。"""
    requires_human_confirm: bool

    def apply_budget_change(self, dim: str, value: str, action: str, memo: str) -> dict:
        ...


class StubAdSpendAdapter:
    """占位：只把「该怎么调预算」写成一条待人执行的指令，不碰任何广告平台/钱。"""
    requires_human_confirm = True

    def apply_budget_change(self, dim: str, value: str, action: str, memo: str) -> dict:
        return {"dim": dim, "value": value, "action": action,
                "instruction": f"[待人执行] 对 {dim}={value} 执行「{action}」：{memo}",
                "requires_human_confirm": True}


class MarketingAgent:
    def __init__(self, db: Database, ad_adapter: Optional[AdSpendAdapter] = None):
        self.db = db
        self.ad = ad_adapter or StubAdSpendAdapter()

    # ---------------- 全阶段维度漏斗 ---------------- #
    def dimension_funnel(self, dim: str) -> list[dict]:
        """按维度值切**全阶段**漏斗：咨询/有效/报价/成单 + 成交额 + 各段转化率。"""
        if dim not in DIMENSIONS:
            raise ValueError(f"维度只能是 {DIMENSIONS}，收到 {dim!r}")
        rows = self.db.conn.execute(
            f"SELECT {dim} v, "
            "COUNT(DISTINCT CASE WHEN event_type='inquiry' THEN lead_id END) inquiry, "
            "COUNT(DISTINCT CASE WHEN event_type='valid'   THEN lead_id END) valid, "
            "COUNT(DISTINCT CASE WHEN event_type='quoted'  THEN lead_id END) quoted, "
            "COUNT(DISTINCT CASE WHEN event_type='won'     THEN lead_id END) won, "
            "COALESCE(SUM(CASE WHEN event_type='won' THEN amount END),0) gmv "
            f"FROM funnel_events GROUP BY {dim}"
        ).fetchall()
        out = []
        for r in rows:
            out.append({
                dim: r["v"] or "(未标)",
                "inquiry": r["inquiry"], "valid": r["valid"],
                "quoted": r["quoted"], "won": r["won"], "gmv": r["gmv"],
                "rates": {
                    "valid/inquiry": _rate(r["valid"], r["inquiry"]),
                    "quoted/valid": _rate(r["quoted"], r["valid"]),
                    "won/quoted": _rate(r["won"], r["quoted"]),
                    "won/inquiry": _rate(r["won"], r["inquiry"]),
                },
            })
        return sorted(out, key=lambda x: (-x["rates"]["won/inquiry"], -x["gmv"]))

    # ---------------- 预算调整建议（可执行、结构化）---------------- #
    def budget_recommendations(self, dim: str,
                               spend_by_value: Optional[dict[str, int]] = None,
                               min_inquiry: int = 1) -> list[dict]:
        """每个维度值给一条结构化建议。有花费→按 ROAS，无花费→按成交转化率。

        action ∈ {SCALE_UP, HOLD, SHRINK, PAUSE}；均为**建议**，执行是人审的事。
        """
        stats = [s for s in self.dimension_funnel(dim) if s["inquiry"] >= min_inquiry]
        if not stats:
            return []
        spend_by_value = spend_by_value or {}
        recs = []
        for s in stats:
            val = s[dim]
            won_rate = s["rates"]["won/inquiry"]
            spend = spend_by_value.get(val)
            cpa = round(spend / s["won"], 2) if spend and s["won"] else None  # 每单成本(分)
            roas = round(s["gmv"] / spend, 2) if spend else None              # 成交额/花费
            recs.append({"dim": dim, "value": val, "inquiry": s["inquiry"],
                         "won": s["won"], "won_rate": won_rate, "gmv": s["gmv"],
                         "spend": spend, "cpa": cpa, "roas": roas})

        # 排序依据：有全量花费→ROAS；否则→成交转化率（诚实降级）
        has_spend = all(r["roas"] is not None for r in recs)
        recs.sort(key=lambda r: (-(r["roas"] if has_spend else r["won_rate"]), -r["gmv"]))

        best_key = recs[0]["roas"] if has_spend else recs[0]["won_rate"]
        for i, r in enumerate(recs):
            key = r["roas"] if has_spend else r["won_rate"]
            if r["won"] == 0 and r["inquiry"] >= max(3, min_inquiry):
                r["action"] = "PAUSE"
                r["reason"] = f"{r['inquiry']} 次咨询 0 成交 → 暂停，排查素材/受众"
            elif has_spend and r["roas"] is not None and r["roas"] < 1.0:
                r["action"] = "SHRINK"
                r["reason"] = f"ROAS {r['roas']}<1（花的比成交多）→ 降预算"
            elif i == 0 and key > 0:
                r["action"] = "SCALE_UP"
                r["reason"] = (f"表现最好（{'ROAS '+str(r['roas']) if has_spend else f'转化{key:.0%}'}）"
                               f"→ 提预算/多投")
            elif key >= best_key * 0.6 and key > 0:
                r["action"] = "HOLD"
                r["reason"] = "中上表现 → 维持预算"
            else:
                r["action"] = "SHRINK"
                r["reason"] = f"明显低于最优（{key:.0%} vs {best_key:.0%}）→ 降预算/调素材"
        return recs

    def reallocation(self, dim: str, spend_by_value: dict[str, int],
                     shift_pct: int = 30) -> Optional[dict]:
        """把预算从最差 ROAS 挪一部分到最好 ROAS —— 具体数字的调仓建议（需花费数据）。"""
        recs = self.budget_recommendations(dim, spend_by_value)
        priced = [r for r in recs if r["roas"] is not None]
        if len(priced) < 2:
            return None
        best, worst = priced[0], priced[-1]
        if best["value"] == worst["value"] or worst["spend"] in (None, 0):
            return None
        move = int(worst["spend"] * shift_pct / 100)
        return {
            "from": worst["value"], "from_roas": worst["roas"],
            "to": best["value"], "to_roas": best["roas"],
            "move_fen": move, "move_yuan": move / 100,
            "instruction": (f"[待人审执行] {dim}：从 {worst['value']}(ROAS {worst['roas']}) "
                            f"挪 {move/100:.0f} 元预算到 {best['value']}(ROAS {best['roas']})"),
        }

    # ---------------- 「反馈前端市场」总报告 ---------------- #
    def feedback_report(self, dims: tuple = DIMENSIONS,
                        spend: Optional[dict[str, dict[str, int]]] = None) -> dict:
        """V2 一·3 的反馈产物：整体漏斗 + 各维度全阶段拆解 + 可执行预算建议。"""
        spend = spend or {}
        overall_counts = {}
        for st in STAGES:
            overall_counts[st] = self.db.conn.execute(
                "SELECT COUNT(DISTINCT COALESCE(lead_id,id)) c FROM funnel_events "
                "WHERE event_type=?", (st,)).fetchone()["c"]
        gmv = self.db.conn.execute(
            "SELECT COALESCE(SUM(amount),0) s FROM funnel_events WHERE event_type='won'"
        ).fetchone()["s"]
        report = {
            "overall": {
                "counts": overall_counts, "gmv": gmv,
                "rates": {
                    "valid/inquiry": _rate(overall_counts["valid"], overall_counts["inquiry"]),
                    "quoted/valid": _rate(overall_counts["quoted"], overall_counts["valid"]),
                    "won/quoted": _rate(overall_counts["won"], overall_counts["quoted"]),
                    "won/inquiry": _rate(overall_counts["won"], overall_counts["inquiry"]),
                },
            },
            "by_dimension": {},
        }
        for d in dims:
            report["by_dimension"][d] = {
                "funnel": self.dimension_funnel(d),
                "recommendations": self.budget_recommendations(d, spend.get(d)),
            }
        return report
