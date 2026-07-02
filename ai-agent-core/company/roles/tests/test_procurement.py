"""供应商下单 agent 核心测试（离线；下单守卫 + 退改政策分档）。"""

from __future__ import annotations

import pytest

from company.record.db import Database
from company.record.models import (Supplier, PriceEntry, Lead, Quote, QuoteItem, Order)
from company.record import costing
from company.roles.procurement import ProcurementAgent

Y = 100


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    yield d
    d.close()


def _chain(db, deposit_paid=True):
    """建 供应商→价格→线索→报价→订单，返回 (quote_id, order_id)。"""
    sup = db.add_supplier(Supplier(name="S", types=["hotel"], service_score=3))
    pb = db.add_price(PriceEntry(city="北京", resource_type="hotel", supplier_id=sup,
                                 date_start="2026-09-01", date_end="2026-12-31", cost=1000 * Y))
    lead = db.add_lead(Lead(pax_count=2, ages="30", depart_date="2026-10-01",
                            duration_days=3, cities=["北京"], has_flight=True,
                            has_budget=False, status="qualified"))
    items = [QuoteItem(price_book_id=pb, city="北京", resource_type="hotel",
                       unit_cost=1000 * Y, qty=3)]
    q = Quote(lead_id=lead, total_cost=3000 * Y, quote_price=3000 * Y,
              commitments={"guaranteed": ["5星"], "not_guaranteed": ["具体酒店"]})
    qid = db.add_quote(q, items)
    oid = db.add_order(Order(quote_id=qid, needs_advance_transport=True, deposit_pct=40,
                             deposit_amount=1200 * Y, balance_amount=1800 * Y,
                             deposit_status="paid" if deposit_paid else "unpaid"))
    return qid, oid


def test_plan_bookings_pending(db):
    qid, oid = _chain(db)
    agent = ProcurementAgent(db)
    ids = agent.plan_bookings(qid, oid)
    assert len(ids) == 1
    assert agent.summary(qid) == {"pending": 1}


def test_confirm_requires_deposit_paid(db):
    qid, oid = _chain(db, deposit_paid=False)
    agent = ProcurementAgent(db)
    bid = agent.plan_bookings(qid, oid)[0]
    with pytest.raises(ValueError):   # 定金未付不能下单
        agent.confirm_booking(bid)


def test_confirm_when_paid_and_human_gated(db):
    qid, oid = _chain(db, deposit_paid=True)
    agent = ProcurementAgent(db)
    bid = agent.plan_bookings(qid, oid)[0]
    r = agent.confirm_booking(bid)
    assert r["status"] == "confirmed"
    assert r["requires_human_confirm"] is True   # 真下单要人审
    assert agent.summary(qid) == {"confirmed": 1}


def test_cancel_with_refund_policy(db):
    qid, oid = _chain(db)
    # 配一档退改政策：酒店 >=15 天退 100%
    db.conn.execute("INSERT INTO refund_policy(resource_type,days_before_min,"
                    "days_before_max,refund_pct) VALUES('hotel',15,NULL,100)")
    db.conn.commit()
    agent = ProcurementAgent(db)
    bid = agent.plan_bookings(qid, oid)[0]
    r = agent.cancel_booking(bid, days_before=20)
    assert r["refund_pct"] == 100 and r["refund_amount"] == 3000 * Y


def test_cancel_without_policy_is_honest(db):
    qid, oid = _chain(db)
    agent = ProcurementAgent(db)
    bid = agent.plan_bookings(qid, oid)[0]
    r = agent.cancel_booking(bid, days_before=5)   # 没配政策
    assert r["refund"] is None and "未配置" in r["note"]
