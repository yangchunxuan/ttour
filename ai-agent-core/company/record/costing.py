"""确定性成本汇总——这些是纯算术，不是 LLM 的活。

日后 LLM agent 只负责「选哪个供应商 / 定什么策略」这类判断，钱的加总由这里
确定性计算，防止模型幻觉出一个错的总价。
"""

from __future__ import annotations

from .models import Quote, QuoteItem, VALID_DEPOSIT_PCT


def line_cost(item: QuoteItem) -> int:
    """单行成本 = 单价 × 数量（分）。"""
    return int(item.unit_cost) * int(item.qty)


def roll_up(items: list[QuoteItem]) -> int:
    """把明细行加总成总成本（分）。"""
    return sum(line_cost(it) for it in items)


def apply_margin(total_cost: int, markup_pct: float) -> tuple[int, int]:
    """按加价率算利润和对客价。返回 (margin, quote_price)，都是分。

    markup_pct = 0.20 表示在成本上加 20%。
    """
    margin = round(total_cost * markup_pct)
    return margin, total_cost + margin


def pick_deposit_pct(needs_advance_transport: bool) -> int:
    """定金比例（V2 四）：需提前订国内交通 → 40%，否则 30%。"""
    pct = 40 if needs_advance_transport else 30
    assert pct in VALID_DEPOSIT_PCT
    return pct


def deposit_amount(quote_price: int, deposit_pct: int) -> int:
    """定金额（分）= 对客价 × 定金比例。"""
    return round(quote_price * deposit_pct / 100)
