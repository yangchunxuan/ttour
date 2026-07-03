"""PayPal 收款适配器测试：用注入的假 transport 验证「照真 API 建请求 + 只读对账」，
不碰真网络/不需真凭证/绝不划钱。同时验证它能直插 CollectionAgent（协议对齐）。
"""

from __future__ import annotations

import pytest

from company.record.db import Database
from company.record.models import Supplier, PriceEntry, Order
from company.roles.collections import CollectionAgent
from company.systems.paypal import PayPalPaymentAdapter, _id_from_href, PayPalError


class FakePayPal:
    """记录每次调用，按路径返回预设响应。"""
    def __init__(self):
        self.calls = []
        self.status = "SENT"

    def __call__(self, method, path, payload):
        self.calls.append((method, path, payload))
        if path == "/v2/invoicing/invoices" and method == "POST":
            return {"id": "INV2-TEST-0001", "href":
                    "https://api-m.sandbox.paypal.com/v2/invoicing/invoices/INV2-TEST-0001"}
        if path.endswith("/send"):
            return {}
        if method == "GET":
            return {"id": "INV2-TEST-0001", "status": self.status,
                    "due_amount": {"currency_code": "USD", "value": "1920.00"}}
        return {}


def test_create_draft_invoice_builds_real_request():
    fake = FakePayPal()
    pp = PayPalPaymentAdapter("cid", "sec", currency="USD",
                              invoicer_email="ops@ttour.com", transport=fake)
    req = pp.create_payment_request(192000, "paypal", "订单#7 40% 定金",
                                    recipient_email="guest@x.com")
    # 命中真端点
    method, path, payload = fake.calls[0]
    assert method == "POST" and path == "/v2/invoicing/invoices"
    # 载荷字段照官方契约
    assert payload["detail"]["currency_code"] == "USD"
    assert payload["items"][0]["unit_amount"] == {"currency_code": "USD", "value": "1920.00"}
    assert payload["invoicer"]["email_address"] == "ops@ttour.com"
    assert payload["primary_recipients"][0]["billing_info"]["email_address"] == "guest@x.com"
    # 返回给收款流程的结构
    assert req["provider"] == "paypal" and req["provider_ref"] == "INV2-TEST-0001"
    assert req["requires_human_confirm"] is True


def test_amount_cents_to_two_decimal_value():
    fake = FakePayPal()
    pp = PayPalPaymentAdapter(transport=fake, currency="CNY")
    pp.create_payment_request(576050, "paypal", "尾款")   # 5760.50
    assert fake.calls[0][2]["items"][0]["unit_amount"]["value"] == "5760.50"


def test_send_invoice_hits_send_endpoint():
    fake = FakePayPal()
    pp = PayPalPaymentAdapter(transport=fake)
    out = pp.send_invoice("INV2-TEST-0001")
    assert out == {"provider_ref": "INV2-TEST-0001", "sent": True}
    assert fake.calls[-1][1] == "/v2/invoicing/invoices/INV2-TEST-0001/send"


def test_check_status_readonly_paid_mapping():
    fake = FakePayPal()
    pp = PayPalPaymentAdapter(transport=fake)
    fake.status = "SENT"
    assert pp.check_status("INV2-TEST-0001")["paid"] is False
    fake.status = "PAID"
    s = pp.check_status("INV2-TEST-0001")
    assert s["paid"] is True and s["status"] == "PAID" and s["due_value"] == "1920.00"
    # 全程只 GET，未 POST 任何 capture/划钱端点
    assert all(m == "GET" for m, p, _ in fake.calls)


def test_never_calls_money_movement_endpoints():
    fake = FakePayPal()
    pp = PayPalPaymentAdapter(transport=fake)
    pp.create_payment_request(10000, "paypal", "定金")
    pp.send_invoice("INV2-TEST-0001")
    pp.check_status("INV2-TEST-0001")
    paths = [p for _, p, _ in fake.calls]
    assert not any("capture" in p or "payout" in p or "refund" in p for p in paths)


def test_drops_into_collection_agent():
    """协议对齐：PayPalAdapter 能直接替换 StubPaymentAdapter 驱动收款流程。"""
    db = Database(":memory:")
    db.conn.execute("PRAGMA foreign_keys=OFF")  # 本测只验适配器协议对接，不测报价FK
    fake = FakePayPal()
    # 建一张最小订单
    oid = db.add_order(Order(quote_id=1, needs_advance_transport=True, deposit_pct=40,
                             deposit_amount=192000, balance_amount=288000, pay_method="paypal"))
    coll = CollectionAgent(db, PayPalPaymentAdapter("cid", "sec", transport=fake))
    r = coll.request_deposit(oid)
    assert r["kind"] == "deposit" and r["provider"] == "paypal"
    assert r["provider_ref"] == "INV2-TEST-0001"
    db.close()


def test_id_from_href_helper():
    assert _id_from_href("https://x/v2/invoicing/invoices/INV2-ABC") == "INV2-ABC"
    assert _id_from_href("") == ""


def test_missing_token_raises(monkeypatch):
    # 无 transport（走真 OAuth 路径）但断网/无凭证时应给清晰错误，而不是静默
    pp = PayPalPaymentAdapter("", "", transport=None)
    import company.systems.paypal as m

    def boom(*a, **k):
        raise OSError("no network")
    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises((PayPalError, OSError)):
        pp._access_token()
