"""Phase 0 反例测试：每条不变量注入一个违规 → 必须被拒。

跑：cd ai-agent-core && python3 -m pytest company/record/tests -q
（纯 stdlib + pytest，不需要 VM/网络）
"""

from __future__ import annotations

import pytest

from company.record.db import Database
from company.record.models import (Supplier, PriceEntry, Lead, Quote, QuoteItem,
                                    Order, ResourceType)
from company.record import costing
from company.record.validate import ValidationError

Y = 100


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    yield d
    d.close()


def _supplier(db):
    return db.add_supplier(Supplier(name="S", city_coverage=["北京"], types=["hotel"]))


def _price(db, sup, cost=800 * Y):
    return db.add_price(PriceEntry(city="北京", resource_type="hotel", supplier_id=sup,
                                   date_start="2026-09-01", date_end="2026-12-31", cost=cost))


def _complete_lead(db):
    return db.add_lead(Lead(pax_count=2, ages="30", depart_date="2026-10-01",
                            duration_days=3, cities=["北京"], has_flight=True,
                            has_budget=False, status="qualified"))


def _good_quote(lead_id, pb, cost=800 * Y):
    items = [QuoteItem(price_book_id=pb, city="北京", resource_type="hotel",
                       unit_cost=cost, qty=2)]
    total = costing.roll_up(items)
    q = Quote(lead_id=lead_id, total_cost=total, margin=0, quote_price=total,
              commitments={"guaranteed": ["5星"], "not_guaranteed": ["具体酒店"]})
    return q, items


# ---- 合法数据必须放行 ----
def test_happy_path_passes(db):
    sup = _supplier(db); pb = _price(db, sup); lead = _complete_lead(db)
    q, items = _good_quote(lead, pb)
    qid = db.add_quote(q, items)
    assert qid > 0
    assert db.check_all() == []


# ---- INV-Q1 报价不许编价 ----
def test_inv_q1_nonexistent_price_rejected(db):
    lead = _complete_lead(db)
    items = [QuoteItem(price_book_id=99999, city="北京", resource_type="hotel",
                       unit_cost=800 * Y, qty=1)]
    q = Quote(lead_id=lead, total_cost=800 * Y, quote_price=800 * Y,
              commitments={"guaranteed": ["x"], "not_guaranteed": ["y"]})
    with pytest.raises(ValidationError) as e:
        db.add_quote(q, items)
    assert e.value.inv_id == "INV-Q1"


def test_inv_q1_wrong_unit_cost_rejected(db):
    sup = _supplier(db); pb = _price(db, sup, cost=800 * Y); lead = _complete_lead(db)
    # 编了个和价格库不符的单价
    items = [QuoteItem(price_book_id=pb, city="北京", resource_type="hotel",
                       unit_cost=500 * Y, qty=1)]
    q = Quote(lead_id=lead, total_cost=500 * Y, quote_price=500 * Y,
              commitments={"guaranteed": ["x"], "not_guaranteed": ["y"]})
    with pytest.raises(ValidationError) as e:
        db.add_quote(q, items)
    assert e.value.inv_id == "INV-Q1"


# ---- INV-Q2 总成本确定性 ----
def test_inv_q2_wrong_total_rejected(db):
    sup = _supplier(db); pb = _price(db, sup); lead = _complete_lead(db)
    q, items = _good_quote(lead, pb)
    q.total_cost = q.total_cost + 12345   # 手改总价
    with pytest.raises(ValidationError) as e:
        db.add_quote(q, items)
    assert e.value.inv_id == "INV-Q2"


# ---- INV-Q3 承诺标注 ----
def test_inv_q3_missing_commitments_rejected(db):
    sup = _supplier(db); pb = _price(db, sup); lead = _complete_lead(db)
    q, items = _good_quote(lead, pb)
    q.commitments = {"guaranteed": ["全都保证"]}  # 缺 not_guaranteed
    with pytest.raises(ValidationError) as e:
        db.add_quote(q, items)
    assert e.value.inv_id == "INV-Q3"


def test_inv_q3_empty_not_guaranteed_rejected(db):
    sup = _supplier(db); pb = _price(db, sup); lead = _complete_lead(db)
    q, items = _good_quote(lead, pb)
    q.commitments = {"guaranteed": ["5星"], "not_guaranteed": []}
    with pytest.raises(ValidationError) as e:
        db.add_quote(q, items)
    assert e.value.inv_id == "INV-Q3"


# ---- INV-Q4 报价性质 ----
def test_inv_q4_non_estimate_rejected(db):
    sup = _supplier(db); pb = _price(db, sup); lead = _complete_lead(db)
    q, items = _good_quote(lead, pb)
    q.nature = "locked"
    with pytest.raises(ValidationError) as e:
        db.add_quote(q, items)
    assert e.value.inv_id == "INV-Q4"


# ---- INV-L1 需求完整性 ----
def test_inv_l1_incomplete_lead_rejected(db):
    sup = _supplier(db); pb = _price(db, sup)
    lead = db.add_lead(Lead(pax_count=2, cities=["北京"]))  # 缺日期/天数/机票/预算
    q, items = _good_quote(lead, pb)
    with pytest.raises(ValidationError) as e:
        db.add_quote(q, items)
    assert e.value.inv_id == "INV-L1"


# ---- INV-O1 定金规则 ----
def test_inv_o1_bad_pct_rejected(db):
    with pytest.raises(ValidationError) as e:
        db.add_order(Order(quote_id=1, needs_advance_transport=False,
                           deposit_pct=25, deposit_amount=0))
    assert e.value.inv_id == "INV-O1"


def test_inv_o1_transport_must_be_40(db):
    with pytest.raises(ValidationError) as e:
        db.add_order(Order(quote_id=1, needs_advance_transport=True,
                           deposit_pct=30, deposit_amount=0))
    assert e.value.inv_id == "INV-O1"


def test_deposit_rule_values():
    assert costing.pick_deposit_pct(True) == 40
    assert costing.pick_deposit_pct(False) == 30
