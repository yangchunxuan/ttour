"""运营控制台测试：状态沉库、人在闸门处逐步推进。"""

from __future__ import annotations

import pytest

from company.record.db import Database
from company.record.models import Supplier, PriceEntry
from company.console import (op_intake, op_pending, op_send, op_deposit,
                             op_book, op_balance, op_funnel)

Y = 100
FIELDS = {"pax_count": 2, "ages": "35", "depart_date": "2026-10-05", "duration_days": 4,
          "cities": ["北京", "西安"], "has_flight": False, "has_budget": True,
          "budget_amount": 20000, "intent": "high"}


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    for city, rt, cost in [("北京", "hotel", 800 * Y), ("西安", "hotel", 650 * Y),
                           ("北京", "transport", 550 * Y)]:
        sid = d.add_supplier(Supplier(name=city, types=[rt], service_score=4))
        d.add_price(PriceEntry(city=city, resource_type=rt, supplier_id=sid,
                               date_start="2026-09-01", date_end="2026-12-31", cost=cost))
    yield d
    d.close()


def test_operator_flow_gate_by_gate(db):
    # 进线索 → 停在待发报价
    r = op_intake(db, FIELDS)
    assert r["stage"] == "quote_pending_review"
    qid = r["quote_id"]
    # 待办里能看到它
    p = op_pending(db)
    assert any(x["quote"] == qid for x in p["待发报价(闸门1)"])
    # 闸门1：发 → 建订单 + 生成定金收款单
    s = op_send(db, qid)
    oid = s["order_id"]
    assert "定金收款单" in s
    # 待办变成"待确认定金"
    assert any(x["order"] == oid for x in op_pending(db)["待确认定金(闸门2)"])
    # 闸门2/3/4 逐步推
    op_deposit(db, oid)
    op_book(db, oid)
    fin = op_balance(db, oid)
    assert fin["stage"] == "settled"
    # 全部推完，无待办
    p2 = op_pending(db)
    assert all(len(v) == 0 for v in p2.values())
    # 成交进了漏斗
    assert op_funnel(db)["漏斗"]["counts"]["won"] == 1


def test_intake_collecting_when_incomplete(db):
    r = op_intake(db, {"pax_count": 2, "cities": ["北京"]})
    assert r["stage"] == "collecting" and r["follow_up"]


def test_intake_need_pricebook(db):
    r = op_intake(db, dict(FIELDS, cities=["成都"]))
    assert r["stage"] == "need_pricebook"
