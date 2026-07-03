"""收款自动对账测试：请求收款记下支付方单号 → 查权威状态自动推进（去掉手工确认）。
§3：读支付方 PAID 记录反映事实，非 agent 擅自认钱；对外承诺仍人审。"""

from __future__ import annotations

import pytest

from company.record.db import Database
from company.record.models import Order
from company.roles.collections import CollectionAgent, StubPaymentAdapter
from company.systems.paypal import PayPalPaymentAdapter


class FakePayPal:
    """按发票号返回状态；create 返回递增发票号。"""
    def __init__(self):
        self.calls = []
        self.paid = set()          # 哪些发票号算已付
        self._n = 0

    def __call__(self, method, path, payload):
        self.calls.append((method, path, payload))
        if path == "/v2/invoicing/invoices":
            self._n += 1
            return {"id": f"INV-{self._n}"}
        if method == "GET":
            inv = path.rsplit("/", 1)[-1]
            return {"id": inv, "status": "PAID" if inv in self.paid else "SENT"}
        return {}


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "rec.db")
    d.conn.execute("PRAGMA foreign_keys=OFF")   # 只测收款状态机，不建报价链
    yield d
    d.close()


def _order(db, transport=True):
    return db.add_order(Order(quote_id=1, needs_advance_transport=transport,
                              deposit_pct=40 if transport else 30,
                              deposit_amount=192000, balance_amount=288000,
                              pay_method="paypal"))


def test_request_deposit_stores_provider_ref(db):
    fake = FakePayPal()
    coll = CollectionAgent(db, PayPalPaymentAdapter(transport=fake))
    oid = _order(db)
    coll.request_deposit(oid)
    ref = db.conn.execute("SELECT deposit_ref FROM orders WHERE id=?", (oid,)).fetchone()["deposit_ref"]
    assert ref == "INV-1"


def test_reconcile_advances_only_when_provider_paid(db):
    fake = FakePayPal()
    coll = CollectionAgent(db, PayPalPaymentAdapter(transport=fake))
    oid = _order(db)
    coll.request_deposit(oid)
    # 未付 → 不推进
    assert coll.reconcile_order(oid)["advanced"] == []
    assert db.conn.execute("SELECT deposit_status FROM orders WHERE id=?", (oid,)).fetchone()["deposit_status"] == "unpaid"
    # 支付方标记已付 → 自动推进 deposit_paid
    fake.paid.add("INV-1")
    assert coll.reconcile_order(oid)["advanced"] == ["deposit"]
    assert db.conn.execute("SELECT deposit_status FROM orders WHERE id=?", (oid,)).fetchone()["deposit_status"] == "paid"


def test_reconcile_balance_only_after_deposit(db):
    fake = FakePayPal()
    coll = CollectionAgent(db, PayPalPaymentAdapter(transport=fake))
    oid = _order(db)
    coll.request_deposit(oid)
    fake.paid.add("INV-1")
    coll.reconcile_order(oid)                 # 定金到账
    coll.request_balance(oid)                 # 生成尾款单 INV-2
    fake.paid.add("INV-2")
    assert coll.reconcile_order(oid)["advanced"] == ["balance"]
    assert db.conn.execute("SELECT status FROM orders WHERE id=?", (oid,)).fetchone()["status"] == "settled"


def test_reconcile_all_batch(db):
    fake = FakePayPal()
    coll = CollectionAgent(db, PayPalPaymentAdapter(transport=fake))
    o1, o2 = _order(db), _order(db)
    coll.request_deposit(o1); coll.request_deposit(o2)
    fake.paid.add("INV-1")                    # 只有 o1 付了
    res = coll.reconcile_all()
    assert res["checked"] == 2 and len(res["reconciled"]) == 1
    assert res["reconciled"][0]["order_id"] == o1


def test_stub_adapter_stays_manual(db):
    """无状态查询能力的适配器（占位）→ 对账是 no-op，保持人工确认，不崩。"""
    coll = CollectionAgent(db, StubPaymentAdapter())
    oid = _order(db)
    coll.request_deposit(oid)
    r = coll.reconcile_all()
    assert r["reconciled"] == [] and "人工确认" in r["note"]


def test_reconcile_does_not_book_or_send(db):
    """§3：对账只推进收款状态，绝不触发向供应商下单/对外发送。"""
    fake = FakePayPal()
    coll = CollectionAgent(db, PayPalPaymentAdapter(transport=fake))
    oid = _order(db)
    coll.request_deposit(oid)
    fake.paid.add("INV-1")
    coll.reconcile_order(oid)
    # 没有任何 booking 被创建（对账不碰供应商）
    assert db.conn.execute("SELECT COUNT(*) c FROM bookings").fetchone()["c"] == 0
