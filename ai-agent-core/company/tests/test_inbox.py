"""分层收件箱测试：路由规则 + 注入假 brain（离线，不打网络）。

重点：碰钱是硬闸门（永远找人）、拿不准升级 Claude、常规自理、模型吐坏 JSON 也兜底升级。
"""

from __future__ import annotations

import json

from company.inbox import decide, route_of, Decision


def test_route_money_is_hard_gate_to_human():
    # 碰钱/承诺无条件找人，哪怕模型很自信、说不用升级
    assert route_of(escalate=False, confidence="high", money_or_commitment=True) == "human"


def test_route_low_confidence_or_escalate_goes_to_claude():
    assert route_of(escalate=True, confidence="high", money_or_commitment=False) == "claude"
    assert route_of(escalate=False, confidence="low", money_or_commitment=False) == "claude"


def test_route_routine_is_auto():
    assert route_of(escalate=False, confidence="high", money_or_commitment=False) == "auto"


def _brain(payload: dict):
    return lambda system, user: json.dumps(payload, ensure_ascii=False)


def test_decide_routine_self_serves():
    d = decide(_brain({
        "understood": "夫妻十月去北京西安", "extracted": {"pax_count": 2, "cities": ["北京", "西安"]},
        "reply_draft": "您好，可以安排…", "confidence": "high",
        "escalate": False, "escalate_reason": "", "money_or_commitment": False,
    }), "…")
    assert d.route == "auto" and d.extracted["pax_count"] == 2 and d.reply_draft


def test_decide_money_routes_human():
    d = decide(_brain({
        "understood": "客户要打定金", "extracted": {}, "reply_draft": "我转接同事",
        "confidence": "high", "escalate": True, "escalate_reason": "收款",
        "money_or_commitment": True,
    }), "发我账号打定金")
    assert d.route == "human" and d.money_or_commitment is True


def test_decide_malformed_json_escalates_to_claude():
    # 一号员工没吐合法 JSON 本身就是"拿不准" → 兜底升级二号
    d = decide(lambda s, u: "抱歉我不确定", "…")
    assert d.route == "claude" and d.escalate is True
