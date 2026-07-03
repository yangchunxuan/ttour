"""自主运行层测试：一轮 tick 自动对账 + 汇总待人审队列 + 转化快照；
§3：autopilot 不自动发报价/下单，只把它们排进待办。"""

from __future__ import annotations

import pytest

from company.record.db import Database
from company.record.models import Supplier, PriceEntry, Order
from company.roles.collections import CollectionAgent, StubPaymentAdapter
from company.systems.paypal import PayPalPaymentAdapter
from company.autopilot import tick, run

Y = 100


class FakePayPal:
    def __init__(self):
        self.paid = set()
        self._n = 0

    def __call__(self, method, path, payload):
        if path == "/v2/invoicing/invoices":
            self._n += 1
            return {"id": f"INV-{self._n}"}
        if method == "GET":
            inv = path.rsplit("/", 1)[-1]
            return {"id": inv, "status": "PAID" if inv in self.paid else "SENT"}
        return {}


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "ap.db")
    yield d
    d.close()


def _order(db):
    db.conn.execute("PRAGMA foreign_keys=OFF")
    return db.add_order(Order(quote_id=1, needs_advance_transport=True, deposit_pct=40,
                              deposit_amount=192000, balance_amount=288000, pay_method="paypal"))


def test_tick_auto_reconciles_paid_order(db):
    fake = FakePayPal()
    adapter = PayPalPaymentAdapter(transport=fake)
    coll = CollectionAgent(db, adapter)
    oid = _order(db)
    coll.request_deposit(oid)
    # 未付：tick 不推进，但订单在「待确认定金」待办里
    out = tick(db, adapter)
    assert out["reconciled"] == []
    assert any(x["order"] == oid for x in out["pending_gates"]["待确认定金(闸门2)"])
    # 客户付款后：tick 自动推进，待办里消失
    fake.paid.add("INV-1")
    out2 = tick(db, adapter)
    assert [r["order_id"] for r in out2["reconciled"]] == [oid]
    assert out2["pending_gates"]["待确认定金(闸门2)"] == []


def test_tick_surfaces_pending_quote_but_does_not_send(db):
    """待发报价进 tick 的待办队列，但 autopilot 不自动发（§3）。"""
    # 造一张 pending_review 报价（走真报价链）
    sid = db.add_supplier(Supplier(name="北京", types=["hotel"], service_score=4))
    db.add_price(PriceEntry(city="北京", resource_type="hotel", supplier_id=sid,
                            date_start="2026-09-01", date_end="2026-12-31", cost=800 * Y))
    from company.record.models import Lead, LeadStatus
    from company.roles.operations import OperationsAgent
    lead = db.add_lead(Lead(source="t", pax_count=2, ages="35", depart_date="2026-10-05",
                            duration_days=3, cities=["北京"], has_flight=True, has_budget=False,
                            status=LeadStatus.QUALIFIED.value))
    q = OperationsAgent(db).build_quote(lead, [{"city": "北京", "resource_type": "hotel", "qty": 3}])
    out = tick(db)
    assert any(x["quote"] == q["quote_id"] for x in out["pending_gates"]["待发报价(闸门1)"])
    # 报价仍是 pending_review（没被自动发）
    assert db.conn.execute("SELECT status FROM quotes WHERE id=?",
                           (q["quote_id"],)).fetchone()["status"] == "pending_review"


def test_tick_with_stub_adapter_no_crash(db):
    out = tick(db, StubPaymentAdapter())
    assert out["reconciled"] == [] and "counts" in out["funnel"]


def test_run_once_mode(db, capsys):
    """--once：跑一轮就返回，不起服务、不死循环。"""
    run(db, once=True, payment_adapter=StubPaymentAdapter())
    printed = capsys.readouterr().out
    assert "单轮" in printed


def test_run_loop_bounded_by_max_ticks(db):
    """循环模式能被 max_ticks 收敛（不起 webhook），证明主循环可控退出。"""
    run(db, serve_webhook=False, interval=0, max_ticks=2,
        payment_adapter=StubPaymentAdapter())
    # 跑完不抛异常即通过（主循环受控退出）
