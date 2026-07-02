"""数据分析 agent 核心 —— Phase 3。

职责（V2 一·3）：统计 咨询→有效→报价→成单 的转化漏斗 + 成交金额 + 转化率，
按 广告内容/地区/时间/平台 维度切，产出「优化投放」建议反馈前端市场。

纯读 company.record 的 funnel_events 表做确定性统计，不依赖 SaleSmartly / VM / LLM。
"""

from __future__ import annotations

from company.record.db import Database

STAGES = ["inquiry", "valid", "quoted", "won"]  # 咨询/有效/报价/成单


def _rate(num: int, den: int) -> float:
    return round(num / den, 4) if den else 0.0


class AnalyticsAgent:
    def __init__(self, db: Database):
        self.db = db

    def record_event(self, event_type: str, lead_id: int | None = None,
                     amount: int = 0, ad_content: str = "", region: str = "",
                     campaign_time: str = "", platform: str = "") -> int:
        from datetime import datetime, timezone
        cur = self.db.conn.execute(
            "INSERT INTO funnel_events(event_type,lead_id,amount,ad_content,region,"
            "campaign_time,platform,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (event_type, lead_id, amount, ad_content, region, campaign_time, platform,
             datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        self.db.conn.commit()
        return cur.lastrowid

    def funnel_summary(self) -> dict:
        """整体漏斗：各阶段人数 + 成交金额 + 转化率。"""
        counts = {}
        for st in STAGES:
            counts[st] = self.db.conn.execute(
                "SELECT COUNT(DISTINCT COALESCE(lead_id, id)) c FROM funnel_events "
                "WHERE event_type=?", (st,)
            ).fetchone()["c"]
        gmv = self.db.conn.execute(
            "SELECT COALESCE(SUM(amount),0) s FROM funnel_events WHERE event_type='won'"
        ).fetchone()["s"]
        return {
            "counts": counts,
            "gmv": gmv,
            "rates": {
                "valid/inquiry": _rate(counts["valid"], counts["inquiry"]),
                "quoted/valid": _rate(counts["quoted"], counts["valid"]),
                "won/quoted": _rate(counts["won"], counts["quoted"]),
                "won/inquiry": _rate(counts["won"], counts["inquiry"]),  # 总转化率
            },
        }

    def by_dimension(self, dim: str) -> list[dict]:
        """按维度(platform/ad_content/region/campaign_time)切转化。"""
        assert dim in ("platform", "ad_content", "region", "campaign_time")
        rows = self.db.conn.execute(
            f"SELECT {dim} v, "
            "SUM(event_type='inquiry') inq, SUM(event_type='won') won, "
            "COALESCE(SUM(CASE WHEN event_type='won' THEN amount END),0) gmv "
            f"FROM funnel_events GROUP BY {dim}"
        ).fetchall()
        out = []
        for r in rows:
            out.append({dim: r["v"] or "(未标)", "inquiry": r["inq"], "won": r["won"],
                        "won_rate": _rate(r["won"], r["inq"]), "gmv": r["gmv"]})
        return sorted(out, key=lambda x: (-x["won_rate"], -x["gmv"]))

    def optimization_hints(self, dim: str = "platform", min_inquiry: int = 1) -> list[str]:
        """反馈前端市场：哪个维度值该加投、哪个该砍（V2 一·3）。"""
        stats = [s for s in self.by_dimension(dim) if s["inquiry"] >= min_inquiry]
        if not stats:
            return ["数据不足，暂无优化建议"]
        hints = []
        best = stats[0]
        worst = stats[-1]
        hints.append(f"🟢 加投：{dim}={best[dim]} 转化率最高({best['won_rate']:.0%}, "
                     f"成交{best['gmv']//100}元) → 提预算/多投")
        if worst is not best and worst["won_rate"] < best["won_rate"]:
            hints.append(f"🔴 收缩：{dim}={worst[dim]} 转化率最低({worst['won_rate']:.0%}) "
                         f"→ 调素材/降预算/换地区时间")
        return hints
