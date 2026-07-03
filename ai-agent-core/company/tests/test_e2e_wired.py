"""端到端「接通」集成测试：证明整套公司是**一个连起来的自主系统**，不是零件。

链路：SaleSmartly 入站 webhook（真契约）→ 客服抽需求 → Lead qualified
      → 计调按真实价格库(INV-Q1)出报价 → PayPal 开发票(真 Invoicing 契约)
      → 人审闸门放行 → 定金/下单/尾款 → 结清 + 记成交。
外部 I/O（PayPal HTTP）用可注入假件顶替；其余全是真模块真护栏。
上线时把假件换成真凭证即为真跑——这就是「差最后一公里凭证」的证据。
"""

from __future__ import annotations

import pytest

from company.record.db import Database
from company.record.models import Supplier, PriceEntry
from company.pipeline import (CompanyPipeline, GATE_BOOK_SUPPLIERS)
from company.systems.salesmartly import SaleSmartlyInbound, parse_webhook
from company.systems.paypal import PayPalPaymentAdapter
from company.roles.analytics import AnalyticsAgent

Y = 100

# 一段真实形状的入站消息（Facebook→Messenger channel=1）
WEBHOOK = {
    "event": "message", "chat_user_id": "u_e2e", "chat_session_id": "s_e2e",
    "sequence_id": 1, "msg": "两个人十月五号去北京西安玩四天，机票有了，预算两万",
    "msg_type": "text", "channel": 1, "send_time": "1739000000000",
}
FIELDS = {"pax_count": 2, "ages": "35,33", "depart_date": "2026-10-05", "duration_days": 4,
          "cities": ["北京", "西安"], "has_flight": True, "has_budget": True,
          "budget_amount": 20000, "hotel_level": "5星", "intent": "high"}


class FakePayPal:
    """假 PayPal transport：记录调用、返回预设，绝不碰网络/不划钱。"""
    def __init__(self):
        self.calls = []

    def __call__(self, method, path, payload):
        self.calls.append((method, path, payload))
        if path == "/v2/invoicing/invoices":
            return {"id": "INV2-E2E-1"}
        return {}


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "e2e.db")
    for city, rt, cost in [("北京", "hotel", 800 * Y), ("西安", "hotel", 650 * Y),
                           ("北京", "transport", 550 * Y)]:
        sid = d.add_supplier(Supplier(name=city, types=[rt], service_score=4))
        d.add_price(PriceEntry(city=city, resource_type=rt, supplier_id=sid,
                               date_start="2026-09-01", date_end="2026-12-31", cost=cost))
    yield d
    d.close()


def test_frontdoor_to_settlement_one_wired_system(db):
    # ── 前门：SaleSmartly webhook → 客服 → qualified Lead ──
    inbound = SaleSmartlyInbound(db, lambda c, s: dict(FIELDS))
    r = inbound.handle(parse_webhook(WEBHOOK))
    assert r["status"] == "qualified"
    lead_id = r["lead_id"]
    assert db.get_lead(lead_id).source == "salesmartly:Messenger"

    # ── 中后段：真 PayPal 适配器接进 pipeline，从该 Lead 接手，全放行 ──
    fake = FakePayPal()
    pipe = CompanyPipeline(
        db, extractor=lambda c, s: dict(FIELDS),
        payment_adapter=PayPalPaymentAdapter("cid", "sec", currency="CNY", transport=fake))
    out = pipe.run_from_lead(lead_id, approver=lambda gate, ctx: True, needs_transport=True)

    # 结清 + 成交
    assert out["result"] == "settled" and out["gmv"] > 0
    steps = [t["step"] for t in out["trace"]]
    for s in ("quote_ready", "quote_sent", "order_created", "deposit_paid",
              "suppliers_booked", "settled"):
        assert s in steps

    # PayPal 真被用于开发票（前门→收款 确实接通，不是占位）
    assert any(p == "/v2/invoicing/invoices" for _, p, _ in fake.calls)
    # 定金按 40%（needs_transport=True，INV-O1）→ 发票金额 = gmv*0.4
    inv_payload = next(pl for m, p, pl in fake.calls if p == "/v2/invoicing/invoices")
    expected = f"{out['gmv'] * 0.4 / 100:.2f}"
    assert inv_payload["items"][0]["unit_amount"]["value"] == expected

    # 漏斗记了成交
    assert AnalyticsAgent(db).funnel_summary()["counts"]["won"] == 1


def test_wired_system_holds_at_supplier_gate(db):
    """闸门仍然守住：供应商下单不放行 → 停，不下单、不结清。"""
    inbound = SaleSmartlyInbound(db, lambda c, s: dict(FIELDS))
    lead_id = inbound.handle(parse_webhook(WEBHOOK))["lead_id"]
    fake = FakePayPal()
    pipe = CompanyPipeline(db, extractor=lambda c, s: dict(FIELDS),
                           payment_adapter=PayPalPaymentAdapter(transport=fake))
    out = pipe.run_from_lead(lead_id, approver=lambda g, c: g != GATE_BOOK_SUPPLIERS)
    assert out["result"] == "held" and out["gate"] == GATE_BOOK_SUPPLIERS
    assert db.conn.execute("SELECT COUNT(*) c FROM bookings WHERE status='confirmed'"
                           ).fetchone()["c"] == 0
