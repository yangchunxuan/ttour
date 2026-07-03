"""客服 agent 核心测试（离线：假提取器，不需 VM/key/SaleSmartly）。

cd ai-agent-core && python3 -m pytest company/roles/tests -q
"""

from __future__ import annotations

import pytest

from company.record.db import Database
from company.record.models import LeadStatus
from company.roles.customer_service import (
    CustomerServiceAgent, missing_fields, follow_up_question, build_lead,
)


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    yield d
    d.close()


def fake_extractor(fields):
    """返回一个总是吐 fields 的假提取器。"""
    return lambda conv, schema: dict(fields)


# 一段"信息齐全"的对话 → qualified lead 落库
def test_complete_conversation_makes_qualified_lead(db):
    complete = {
        "pax_count": 2, "ages": "35,33", "depart_date": "2026-10-05",
        "duration_days": 5, "cities": ["北京", "西安"], "has_flight": True,
        "has_budget": True, "budget_amount": 20000, "hotel_level": "5星",
        "intent": "high",
    }
    agent = CustomerServiceAgent(db, fake_extractor(complete))
    r = agent.ingest("客户：两个人，35和33岁，10月5号出发玩5天，去北京西安，机票有了，预算2万",
                     source="ss-conv-1")
    assert r["status"] == LeadStatus.QUALIFIED.value
    assert r["missing"] == []
    assert r["lead_id"] > 0
    # 落库确认 + 元→分
    lead = db.get_lead(r["lead_id"])
    assert lead.pax_count == 2 and lead.cities == ["北京", "西安"]
    assert lead.budget_amount == 20000 * 100


# 一段"信息不全"的对话 → collecting + 指出缺什么 + 给追问
def test_incomplete_conversation_asks_followup(db):
    partial = {"pax_count": 2, "cities": ["北京"], "intent": "mid"}
    agent = CustomerServiceAgent(db, fake_extractor(partial))
    r = agent.ingest("客户：两个人想去北京", source="ss-conv-2")
    assert r["status"] == LeadStatus.COLLECTING.value
    assert "depart_date" in r["missing"] and "duration_days" in r["missing"]
    assert r["follow_up"]  # 有一句追问
    # collecting 的 lead 也落库（部分信息）
    assert db.get_lead(r["lead_id"]).status == LeadStatus.COLLECTING.value


def test_missing_fields_logic():
    assert "budget_amount" in missing_fields({"has_budget": True})  # 说有预算却没给额度
    assert "has_flight" in missing_fields({})
    full = {"pax_count": 1, "ages": "30", "depart_date": "2026-10-01",
            "duration_days": 3, "cities": ["北京"], "has_flight": False,
            "has_budget": False}
    assert missing_fields(full) == []


def test_followup_is_nonempty_for_missing():
    assert follow_up_question(["depart_date"])
    assert follow_up_question([]) == ""


def test_build_lead_converts_budget_yuan_to_cents():
    lead = build_lead({"has_budget": True, "budget_amount": 15000})
    assert lead.budget_amount == 15000 * 100
