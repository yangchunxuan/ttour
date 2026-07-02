"""流水线（总指挥）测试：一条对话自动流过全公司，人审闸门处停/放行。"""

from __future__ import annotations

import pytest

from company.record.db import Database
from company.record.models import Supplier, PriceEntry
from company.pipeline import (CompanyPipeline, GATE_SEND_QUOTE, GATE_DEPOSIT,
                              GATE_BOOK_SUPPLIERS, GATE_BALANCE)

Y = 100

COMPLETE_FIELDS = {
    "pax_count": 2, "ages": "35,33", "depart_date": "2026-10-05", "duration_days": 4,
    "cities": ["北京", "西安"], "has_flight": False, "has_budget": True,
    "budget_amount": 20000, "hotel_level": "5星", "intent": "high",
}


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    # 价格库：北京/西安酒店 + 高铁
    for city, rt, cost in [("北京", "hotel", 800 * Y), ("西安", "hotel", 650 * Y),
                           ("北京", "transport", 550 * Y)]:
        sid = d.add_supplier(Supplier(name=city, types=[rt], service_score=4))
        d.add_price(PriceEntry(city=city, resource_type=rt, supplier_id=sid,
                               date_start="2026-09-01", date_end="2026-12-31", cost=cost))
    yield d
    d.close()


def _pipe(db, fields):
    return CompanyPipeline(db, extractor=lambda c, s: dict(fields))


def test_full_auto_run_all_gates_approved(db):
    """人审全放行 → 一条对话自动跑到结清 + 记成交。"""
    pipe = _pipe(db, COMPLETE_FIELDS)
    r = pipe.run("客户对话…", approver=lambda gate, ctx: True,
                 source="ss-1", platform="facebook")
    assert r["result"] == "settled"
    assert r["gmv"] > 0
    steps = [t["step"] for t in r["trace"]]
    assert "quote_sent" in steps and "deposit_paid" in steps
    assert "suppliers_booked" in steps and "settled" in steps
    # funnel 记了 inquiry→valid→quoted→won
    from company.roles.analytics import AnalyticsAgent
    s = AnalyticsAgent(db).funnel_summary()
    assert s["counts"]["won"] == 1 and s["gmv"] == r["gmv"]


def test_held_at_deposit_gate(db):
    """定金闸门不放行 → 停在那里，不继续下单。"""
    pipe = _pipe(db, COMPLETE_FIELDS)
    r = pipe.run("…", approver=lambda gate, ctx: gate != GATE_DEPOSIT)
    assert r["result"] == "held" and r["gate"] == GATE_DEPOSIT
    # 没有向供应商下单
    assert db.conn.execute("SELECT COUNT(*) c FROM bookings").fetchone()["c"] == 0


def test_collecting_when_info_incomplete(db):
    """信息不全 → 停在收集阶段，不进报价。"""
    partial = {"pax_count": 2, "cities": ["北京"], "intent": "mid"}
    pipe = _pipe(db, partial)
    r = pipe.run("…", approver=lambda g, c: True)
    assert r["result"] == "collecting"
    assert db.conn.execute("SELECT COUNT(*) c FROM quotes").fetchone()["c"] == 0


def test_need_pricebook_when_missing_price(db):
    """价格库缺资源 → 计调诚实报缺，不编价、不出报价。"""
    fields = dict(COMPLETE_FIELDS, cities=["成都"])  # 成都没价格库
    pipe = _pipe(db, fields)
    r = pipe.run("…", approver=lambda g, c: True)
    assert r["result"] == "need_pricebook"
    assert any("成都" in m for m in r["missing"])
