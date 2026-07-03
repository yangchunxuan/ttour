"""对客说明与话术（V2 三·4 对客户 / 三·5 第三阶段）—— 用户给定的谨慎措辞，逐字固化。

为什么单独一个模块：报价说明和取消政策是**合规敏感**的对客话术，措辞要精确、
一致，不能让客服 agent 自由发挥。用户明确定了两条边界：
  1) 预估报价：酒店星级可保证，但具体酒店/房型/加床以预订时库存为准（不锁死）。
  2) 取消：保留强约束，但**不建议**直接说「取消都必须全额付款」——要合理、不吓退。
本模块把这两段原话固化，并加一道守卫：对客话术里不得出现「全额付款」式强硬措辞。

真实退款分档数字（差多少天退多少）是**你未决策的业务规则**（你自述「需完善」），
不在这里编——refund.refund_pct 未配置时返回 None，诚实待你给数字。
"""

from __future__ import annotations

# 用户逐字给定的对客话术（V2 三·5 第三阶段 / 三·4 对客户）
QUOTE_DISCLOSURE = (
    "这是根据当前资源和客户需求生成的预估报价。酒店星级可以保证，"
    "但具体酒店、房型以及是否能加床，需以预订时的实际库存为准。"
)

CANCELLATION_NOTICE = (
    "一旦确认预订并支付定金，取消或变更可能产生费用；具体以已确认资源和"
    "供应商政策为准。部分已预订项目可能无法退款。"
)

# 用户明确不建议对客户使用的强硬措辞（守卫用）
_FORBIDDEN_TO_CUSTOMER = ("全额付款", "必须全额", "一律不退", "概不退款")


def _assert_customer_safe(text: str) -> None:
    hit = [w for w in _FORBIDDEN_TO_CUSTOMER if w in text]
    if hit:
        raise ValueError(f"对客话术不得包含强硬措辞 {hit}（V2 三·4：保留约束但别吓退客户）")


def quote_message(price_yuan: float, commitments: dict | None = None) -> str:
    """客服把报价发给客户时的完整话术：金额 + 预估说明 + 能/不能承诺项。"""
    lines = [f"您好，为您初步核算的行程报价约为 {price_yuan:.0f} 元。", QUOTE_DISCLOSURE]
    if commitments:
        g = "、".join(commitments.get("guaranteed", []))
        ng = "、".join(commitments.get("not_guaranteed", []))
        if g:
            lines.append(f"可以为您保证：{g}。")
        if ng:
            lines.append(f"以下需以预订时实际情况为准：{ng}。")
    msg = "\n".join(lines)
    _assert_customer_safe(msg)
    return msg


def cancellation_message() -> str:
    """对客户解释取消/变更政策时的统一话术（谨慎、不吓退）。"""
    _assert_customer_safe(CANCELLATION_NOTICE)
    return CANCELLATION_NOTICE
