"""计调（Operations）agent 核心 —— Phase 2。

职责（V2 二）：拿一条 qualified Lead + 价格库 → 按「成本×服务」选供应商 →
组一张预估报价单 → 走 Phase 0 护栏落库（draft/pending_review，发客户前人审）。

不依赖 SaleSmartly / VM：纯读 company.record 真相源做确定性组价。
「去外部网页查实时价格 / 下单」是后续 VM 里的桌面控制，不在本核心。

诚实原则：价格库里查不到的资源，**不编价** → 报 incomplete_pricebook + 列缺哪些，
而不是瞎凑一个数（对应 INV-Q1 的精神）。
"""

from __future__ import annotations

from company.record.db import Database
from company.record.models import Quote, QuoteItem, QuoteStatus
from company.record import costing


DEFAULT_MARKUP = 0.20  # 默认加价 20%，可被 pricing_strategy.markup_default 覆盖


def get_markup(db: Database) -> float:
    row = db.conn.execute(
        "SELECT value FROM pricing_strategy WHERE key='markup_default'"
    ).fetchone()
    try:
        return float(row["value"]) if row else DEFAULT_MARKUP
    except Exception:
        return DEFAULT_MARKUP


def _supplier(db: Database, supplier_id: int):
    return db.conn.execute(
        "SELECT * FROM suppliers WHERE id=?", (supplier_id,)
    ).fetchone()


def score_entry(db: Database, entry) -> float:
    """给一条价格库条目按其供应商打「成本×服务」平衡分（V2 二·4）。
    偏好服务/稳定，惩罚高价——不是只挑最便宜的。"""
    sup = _supplier(db, entry["supplier_id"])
    if not sup:
        return -1e9
    return sup["service_score"] * 2 + sup["stability"] - sup["price_level"]


def pick_entry(db: Database, entries: list):
    """从同一(城市,资源类型)的多条报价里选一条平衡的（非最便宜也非最贵）。"""
    if not entries:
        return None
    if len(entries) == 1:
        return entries[0]
    # 按平衡分选；同分选较低成本
    return max(entries, key=lambda e: (score_entry(db, e), -e["cost"]))


def default_commitments() -> dict:
    """V2 三·2/3：能承诺 vs 不能承诺。"""
    return {
        "guaranteed": ["酒店星级", "房型", "床型（尽量满足）"],
        "not_guaranteed": ["具体酒店名称", "是否一定能加床", "临近日期房间库存"],
    }


class OperationsAgent:
    def __init__(self, db: Database):
        self.db = db

    def build_quote(self, lead_id: int, plan: list[dict],
                    markup: float | None = None) -> dict:
        """plan: [{city, resource_type, qty, spec_filter?}]。
        返回 {status, quote_id?, missing?, total?, price?}。"""
        markup = get_markup(self.db) if markup is None else markup
        items: list[QuoteItem] = []
        missing: list[str] = []

        for step in plan:
            entries = self.db.query_price(step["city"], step["resource_type"])
            # 可选 spec 过滤（如 hotel star）
            sf = step.get("spec_filter") or {}
            if sf:
                import json as _json
                entries = [e for e in entries
                           if all(_json.loads(e["spec"] or "{}").get(k) == v
                                  for k, v in sf.items())]
            picked = pick_entry(self.db, entries)
            if picked is None:
                missing.append(f"{step['city']}/{step['resource_type']}"
                               + (f"/{sf}" if sf else ""))
                continue
            items.append(QuoteItem(
                price_book_id=picked["id"], city=step["city"],
                resource_type=step["resource_type"], qty=int(step.get("qty", 1)),
                unit_cost=picked["cost"]))

        if missing:
            # 诚实：查不到就不编价，报缺口给上层（人工补价格库 / 换资源）
            return {"status": "incomplete_pricebook", "missing": missing}

        total = costing.roll_up(items)
        margin, price = costing.apply_margin(total, markup)
        lead = self.db.get_lead(lead_id)
        itinerary = [{"city": c, "route_order": i + 1}
                     for i, c in enumerate(lead.cities if lead else [])]
        q = Quote(lead_id=lead_id, itinerary=itinerary, total_cost=total,
                  margin=margin, quote_price=price,
                  commitments=default_commitments(),
                  status=QuoteStatus.PENDING_REVIEW.value)  # 发客户前人审（§3 业务闸门）
        qid = self.db.add_quote(q, items)  # 走 INV-Q1/Q2/Q3/Q4 护栏
        return {"status": "quoted", "quote_id": qid, "total": total,
                "margin": margin, "price": price,
                "supplier_ids": [it.price_book_id for it in items]}
