"""边界不变量（护栏）。db.py 在写入边界调用这些；也可单独跑巡检。

核心是 INV-Q1「报价不许编价」——报价里每项成本必须来自价格库真实条目，
等价于 macos_agent 里「文件必须真存在」的硬判据。
"""

from __future__ import annotations

from .models import Lead, Quote, QuoteItem, QUOTE_NATURE_ESTIMATE, VALID_DEPOSIT_PCT
from . import costing


class ValidationError(ValueError):
    """带不变量 id 的校验失败。"""

    def __init__(self, inv_id: str, message: str):
        self.inv_id = inv_id
        super().__init__(f"[{inv_id}] {message}")


# INV-L1 需求 8 项基础信息（V2 一·2）
LEAD_REQUIRED = ["pax_count", "ages", "depart_date", "duration_days"]


def validate_lead_complete(lead: Lead) -> None:
    """INV-L1：lead 推进到 quoted 前，8 项基础信息必须齐。"""
    missing = [f for f in LEAD_REQUIRED if getattr(lead, f) in (None, "")]
    if not lead.cities:
        missing.append("cities")
    if lead.has_flight is None:
        missing.append("has_flight")
    if lead.has_budget is None:
        missing.append("has_budget")
    if lead.has_budget and lead.budget_amount in (None, 0):
        missing.append("budget_amount")
    if missing:
        raise ValidationError(
            "INV-L1",
            f"线索缺字段 {missing}，不能进入报价阶段 → 先让客服 agent 补齐这些基础信息",
        )


def validate_item_price(item: QuoteItem, pricebook_cost: int) -> None:
    """INV-Q1：报价项单价必须与价格库真实条目一致（不许编价）。"""
    if int(item.unit_cost) != int(pricebook_cost):
        raise ValidationError(
            "INV-Q1",
            f"报价项 price_book_id={item.price_book_id} 的 unit_cost={item.unit_cost} "
            f"与价格库 cost={pricebook_cost} 不符 → 只能引用真实 price_book 条目、别编价",
        )


def validate_total_cost(quote: Quote, items: list[QuoteItem]) -> None:
    """INV-Q2：total_cost 必须等于明细确定性汇总。"""
    computed = costing.roll_up(items)
    if int(quote.total_cost) != int(computed):
        raise ValidationError(
            "INV-Q2",
            f"total_cost={quote.total_cost} 与明细汇总 {computed} 不符 "
            f"→ 用 costing.roll_up() 计算，别手填",
        )


def validate_quote_commitments(quote: Quote) -> None:
    """INV-Q3：报价必须区分能承诺 vs 不能承诺（V2 三·2/3）。"""
    c = quote.commitments or {}
    if "guaranteed" not in c or "not_guaranteed" not in c:
        raise ValidationError(
            "INV-Q3",
            "报价 commitments 必须同时含 guaranteed 和 not_guaranteed 两个清单 "
            "→ 标清能承诺(星级/房型/床型) vs 不能承诺(具体酒店/加床/临期库存)",
        )
    if not c.get("not_guaranteed"):
        raise ValidationError(
            "INV-Q3",
            "not_guaranteed 为空 → 必须诚实列出不保证项(具体酒店/加床/临期库存)，"
            "别把不保证的说成保证",
        )


def validate_quote_nature(quote: Quote) -> None:
    """INV-Q4：报价性质恒为预估（V2 三·1）。"""
    if quote.nature != QUOTE_NATURE_ESTIMATE:
        raise ValidationError(
            "INV-Q4",
            f"报价性质应为 '{QUOTE_NATURE_ESTIMATE}'（预估），当前 '{quote.nature}'",
        )


def validate_deposit(needs_advance_transport: bool, deposit_pct: int) -> None:
    """INV-O1：定金只能 30/40，且需提前订国内交通时必须 40（V2 四）。"""
    if deposit_pct not in VALID_DEPOSIT_PCT:
        raise ValidationError(
            "INV-O1",
            f"定金比例只能是 {VALID_DEPOSIT_PCT}，当前 {deposit_pct}",
        )
    expected = costing.pick_deposit_pct(needs_advance_transport)
    if needs_advance_transport and deposit_pct != 40:
        raise ValidationError(
            "INV-O1",
            f"需提前订国内交通时定金必须 40%，当前 {deposit_pct}%（应为 {expected}）",
        )
