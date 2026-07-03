"""计调 agent 核心测试（离线，纯读价格库组价）。"""

from __future__ import annotations

import pytest

from company.record.db import Database
from company.record.models import Supplier, PriceEntry, Lead
from company.roles.operations import OperationsAgent, pick_entry

Y = 100


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    yield d
    d.close()


def _lead(db):
    return db.add_lead(Lead(pax_count=2, ages="30", depart_date="2026-10-01",
                            duration_days=3, cities=["北京"], has_flight=True,
                            has_budget=False, status="qualified"))


def test_build_quote_from_pricebook(db):
    lead = _lead(db)
    sup = db.add_supplier(Supplier(name="S", city_coverage=["北京"], types=["hotel"],
                                   service_score=4, stability=4, price_level=3))
    db.add_price(PriceEntry(city="北京", resource_type="hotel", supplier_id=sup,
                            date_start="2026-09-01", date_end="2026-12-31", cost=800 * Y,
                            spec={"star": 5}))
    agent = OperationsAgent(db)
    r = agent.build_quote(lead, [{"city": "北京", "resource_type": "hotel", "qty": 3}])
    assert r["status"] == "quoted"
    assert r["total"] == 800 * Y * 3
    assert r["price"] == r["total"] + r["margin"]   # 加价后
    # 落库的报价走了护栏、状态待人审
    row = db.conn.execute("SELECT * FROM quotes WHERE id=?", (r["quote_id"],)).fetchone()
    assert row["status"] == "pending_review" and row["nature"] == "estimate"


def test_missing_pricebook_no_fabrication(db):
    """价格库查不到 → 不编价，诚实报缺口。"""
    lead = _lead(db)
    agent = OperationsAgent(db)
    r = agent.build_quote(lead, [{"city": "西安", "resource_type": "hotel", "qty": 2}])
    assert r["status"] == "incomplete_pricebook"
    assert any("西安/hotel" in m for m in r["missing"])
    # 没有伪造报价落库
    assert db.conn.execute("SELECT COUNT(*) c FROM quotes").fetchone()["c"] == 0


def test_supplier_balance_not_cheapest(db):
    """两家同资源：便宜但服务差 vs 稍贵但服务好 → 选平衡的那家（V2 二·4）。"""
    lead = _lead(db)
    cheap_bad = db.add_supplier(Supplier(name="便宜差", types=["hotel"],
                                         service_score=1, stability=1, price_level=1))
    mid_good = db.add_supplier(Supplier(name="稍贵好", types=["hotel"],
                                        service_score=5, stability=5, price_level=3))
    db.add_price(PriceEntry(city="北京", resource_type="hotel", supplier_id=cheap_bad,
                            date_start="2026-09-01", date_end="2026-12-31", cost=500 * Y))
    db.add_price(PriceEntry(city="北京", resource_type="hotel", supplier_id=mid_good,
                            date_start="2026-09-01", date_end="2026-12-31", cost=800 * Y))
    entries = db.query_price("北京", "hotel")
    picked = pick_entry(db, entries)
    # 应选服务好的那家（供应商 mid_good），不是最便宜的
    assert picked["supplier_id"] == mid_good


def test_spec_filter_picks_right_star(db):
    lead = _lead(db)
    sup = db.add_supplier(Supplier(name="S", types=["hotel"], service_score=3))
    db.add_price(PriceEntry(city="北京", resource_type="hotel", supplier_id=sup,
                            date_start="2026-09-01", date_end="2026-12-31", cost=400 * Y,
                            spec={"star": 4}))
    db.add_price(PriceEntry(city="北京", resource_type="hotel", supplier_id=sup,
                            date_start="2026-09-01", date_end="2026-12-31", cost=800 * Y,
                            spec={"star": 5}))
    agent = OperationsAgent(db)
    r = agent.build_quote(lead, [{"city": "北京", "resource_type": "hotel", "qty": 1,
                                  "spec_filter": {"star": 5}}])
    assert r["status"] == "quoted" and r["total"] == 800 * Y  # 选了 5 星那条
