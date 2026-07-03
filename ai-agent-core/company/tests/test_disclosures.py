"""对客话术测试：逐字固化 + 谨慎措辞守卫（不得出现用户禁止的强硬说法）。"""

from __future__ import annotations

import pytest

from company.roles import disclosures as D


def test_quote_disclosure_verbatim():
    # 关键语义：星级可保证，但具体酒店/房型/加床以库存为准（不锁死）
    assert "星级可以保证" in D.QUOTE_DISCLOSURE
    assert "库存为准" in D.QUOTE_DISCLOSURE


def test_cancellation_notice_verbatim_and_soft():
    n = D.cancellation_message()
    assert "支付定金" in n and "供应商政策为准" in n
    # 保留强约束（可能产生费用/可能无法退款）但不吓退
    assert "可能" in n


def test_quote_message_includes_commitments():
    msg = D.quote_message(19200, {"guaranteed": ["酒店星级", "房型"],
                                  "not_guaranteed": ["具体酒店名称", "加床"]})
    assert "19200" in msg and "酒店星级" in msg
    assert "具体酒店名称" in msg and D.QUOTE_DISCLOSURE in msg


def test_guard_blocks_forbidden_harsh_wording():
    # 用户明确不建议：「取消都必须全额付款」式措辞
    with pytest.raises(ValueError):
        D._assert_customer_safe("取消都必须全额付款，一律不退")


def test_message_builders_are_customer_safe():
    # 正常构造的话术不含禁用措辞（守卫内建，不会误伤正常话术）
    D.quote_message(10000, {"guaranteed": ["酒店星级"], "not_guaranteed": ["加床"]})
    D.cancellation_message()
