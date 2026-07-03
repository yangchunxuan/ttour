"""二次报价（V2 低价引流 → 按实际需求重报）测试：版本链、加价调整、行程调整、护栏保持。"""

from __future__ import annotations

import pytest

from company.record.db import Database
from company.record.models import Supplier, PriceEntry, Lead, LeadStatus
from company.roles.operations import OperationsAgent

Y = 100


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "rq.db")
    for city, rt, cost in [("北京", "hotel", 800 * Y), ("西安", "hotel", 650 * Y),
                           ("成都", "hotel", 700 * Y), ("北京", "transport", 550 * Y)]:
        sid = d.add_supplier(Supplier(name=city, types=[rt], service_score=4))
        d.add_price(PriceEntry(city=city, resource_type=rt, supplier_id=sid,
                               date_start="2026-09-01", date_end="2026-12-31", cost=cost))
    yield d
    d.close()


def _lead(db, cities=("北京", "西安")):
    l = Lead(source="t", pax_count=2, ages="35", depart_date="2026-10-05",
             duration_days=4, cities=list(cities), has_flight=False, has_budget=True,
             budget_amount=2000000, status=LeadStatus.QUALIFIED.value)
    return db.add_lead(l)


PLAN = [{"city": "北京", "resource_type": "hotel", "qty": 2},
        {"city": "西安", "resource_type": "hotel", "qty": 2}]


def test_lowball_then_requote_higher(db):
    ops = OperationsAgent(db)
    lead_id = _lead(db)
    v1 = ops.build_quote(lead_id, PLAN, markup=0.05)   # 低价引流：加价 5%
    v2 = ops.re_quote(v1["quote_id"], markup=0.25)      # 二次报价：正常 25%
    assert v2["status"] == "quoted"
    assert v2["version"] == 2 and v2["parent_quote_id"] == v1["quote_id"]
    assert v2["price"] > v1["price"]                    # 二次报价更高
    assert v2["delta"] == v2["price"] - v1["price"] and v2["parent_price"] == v1["price"]
    assert v2["reason"]


def test_requote_reuses_parent_plan_when_none(db):
    ops = OperationsAgent(db)
    lead_id = _lead(db)
    v1 = ops.build_quote(lead_id, PLAN, markup=0.05)
    v2 = ops.re_quote(v1["quote_id"], markup=0.05)     # 同 plan 同加价 → 同价
    assert v2["price"] == v1["price"]                   # 沿用父单行程，成本一致
    # 版本链能查
    chain = ops.quote_history(lead_id)
    assert [c["version"] for c in chain] == [1, 2]
    assert chain[1]["parent_quote_id"] == v1["quote_id"]


def test_requote_with_adjusted_itinerary(db):
    """客户加了成都 → 二次报价按调整后行程重算，价更高。"""
    ops = OperationsAgent(db)
    lead_id = _lead(db, cities=("北京", "西安", "成都"))
    v1 = ops.build_quote(lead_id, PLAN, markup=0.20)
    adjusted = PLAN + [{"city": "成都", "resource_type": "hotel", "qty": 2}]
    v2 = ops.re_quote(v1["quote_id"], plan=adjusted, markup=0.20,
                      reason="客户确认加成都两晚")
    assert v2["status"] == "quoted" and v2["version"] == 2
    assert v2["price"] > v1["price"] and v2["delta"] > 0
    assert v2["reason"] == "客户确认加成都两晚"


def test_requote_honest_when_pricebook_missing(db):
    """二次报价加了没价格库的城市 → 诚实报缺，不编价、不建版本。"""
    ops = OperationsAgent(db)
    lead_id = _lead(db)
    v1 = ops.build_quote(lead_id, PLAN, markup=0.20)
    bad = PLAN + [{"city": "张家界", "resource_type": "hotel", "qty": 2}]
    v2 = ops.re_quote(v1["quote_id"], plan=bad)
    assert v2["status"] == "incomplete_pricebook" and any("张家界" in m for m in v2["missing"])
    # 没建出 v2
    assert len(ops.quote_history(lead_id)) == 1


def test_requote_no_parent(db):
    assert OperationsAgent(db).re_quote(999)["status"] == "no_parent"


def test_requote_still_pending_review(db):
    """二次报价仍是 pending_review：发客户前照样人审（§3 闸门不被绕过）。"""
    ops = OperationsAgent(db)
    lead_id = _lead(db)
    v1 = ops.build_quote(lead_id, PLAN, markup=0.05)
    v2 = ops.re_quote(v1["quote_id"], markup=0.25)
    row = db.conn.execute("SELECT status FROM quotes WHERE id=?", (v2["quote_id"],)).fetchone()
    assert row["status"] == "pending_review"
