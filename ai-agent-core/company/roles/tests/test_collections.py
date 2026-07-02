"""收款 agent 核心测试（离线；证明状态机守卫 + 碰钱要人审）。"""

from __future__ import annotations

import pytest

from company.record.db import Database
from company.record.models import Order, Supplier, PriceEntry, Lead, Quote, QuoteItem
from company.record import costing
from company.roles.collections import CollectionAgent

Y = 100


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    yield d
    d.close()


def _order(db, price=10000 * Y, needs_transport=True):
    # 建完整链：供应商→价格→(信息齐)线索→报价→订单（订单外键指向真报价，符合业务）
    sup = db.add_supplier(Supplier(name="S", types=["hotel"], service_score=3))
    pb = db.add_price(PriceEntry(city="北京", resource_type="hotel", supplier_id=sup,
                                 date_start="2026-09-01", date_end="2026-12-31", cost=price))
    lead = db.add_lead(Lead(pax_count=2, ages="30", depart_date="2026-10-01",
                            duration_days=3, cities=["北京"], has_flight=True,
                            has_budget=False, status="qualified"))
    items = [QuoteItem(price_book_id=pb, city="北京", resource_type="hotel",
                       unit_cost=price, qty=1)]
    q = Quote(lead_id=lead, total_cost=price, quote_price=price,
              commitments={"guaranteed": ["5星"], "not_guaranteed": ["具体酒店"]})
    qid = db.add_quote(q, items)
    pct = costing.pick_deposit_pct(needs_transport)
    dep = costing.deposit_amount(price, pct)
    return db.add_order(Order(quote_id=qid, needs_advance_transport=needs_transport,
                              deposit_pct=pct, deposit_amount=dep,
                              balance_amount=price - dep))


def test_deposit_request_does_not_move_money(db):
    oid = _order(db)
    agent = CollectionAgent(db)
    req = agent.request_deposit(oid)
    assert req["kind"] == "deposit"
    assert req["requires_human_confirm"] is True   # 碰钱必人审
    assert req["amount_yuan"] == 4000              # 10000×40%


def test_full_payment_flow_state_machine(db):
    oid = _order(db)
    agent = CollectionAgent(db)
    agent.request_deposit(oid)
    r1 = agent.confirm_deposit_paid(oid)
    assert r1["status"] == "deposit_paid"
    agent.request_balance(oid)
    r2 = agent.confirm_balance_paid(oid)
    assert r2["status"] == "settled"


def test_cannot_collect_balance_before_deposit(db):
    oid = _order(db)
    agent = CollectionAgent(db)
    with pytest.raises(ValueError):   # 状态机守卫：定金未付不能收尾款
        agent.request_balance(oid)
    with pytest.raises(ValueError):
        agent.confirm_balance_paid(oid)


def test_reconcile(db):
    oid = _order(db, price=10000 * Y)   # 定金 4000, 尾款 6000
    agent = CollectionAgent(db)
    rec0 = agent.reconcile()
    assert rec0["collected"] == 0 and rec0["outstanding"] == 10000 * Y
    agent.confirm_deposit_paid(oid)
    rec1 = agent.reconcile()
    assert rec1["collected"] == 4000 * Y and rec1["outstanding"] == 6000 * Y
