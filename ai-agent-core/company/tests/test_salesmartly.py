"""SaleSmartly 前门适配器测试：照官方 webhook 契约喂真实样例载荷。

覆盖：解析 message 事件、忽略非消息事件、md5 验签、入站幂等去重、
多轮对话更新同一 Lead（不新建）、非文本消息只备档。全离线（假 extractor）。
"""

from __future__ import annotations

import hashlib

import pytest

from company.record.db import Database
from company.systems.salesmartly import (
    InboundMessage, parse_webhook, verify_signature, debug_signature,
    SaleSmartlyInbound, CHANNELS, WEBHOOK_PATH,
)


# 一条真实形状的入站文本消息（Instagram=channel 5）
SAMPLE = {
    "event": "message",
    "chat_user_id": "u_abc",
    "chat_session_id": "s_123",
    "sequence_id": 1001,
    "msg": "你好，我们两个人想十月去北京西安玩四天",
    "msg_type": "text",
    "channel": 5,
    "send_time": "1739000000000",
}


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    yield d
    d.close()


# ---------------- 解析 ---------------- #
def test_parse_real_message():
    m = parse_webhook(SAMPLE)
    assert isinstance(m, InboundMessage)
    assert m.conversation_id == "s_123" and m.contact_id == "u_abc"
    assert m.message_id == 1001 and m.channel == 5
    assert m.channel_name == "Instagram" and m.is_text
    assert "北京西安" in m.text


def test_parse_ignores_non_message_event():
    assert parse_webhook({"event": "customer_update", "chat_user_id": "u"}) is None
    assert parse_webhook({}) is None


def test_channel_map_covers_user_sources():
    # 用户来源：Facebook=Messenger(1)、Instagram(5)
    assert CHANNELS[1] == "Messenger" and CHANNELS[5] == "Instagram"


def test_non_text_msg_kept_as_json():
    body = dict(SAMPLE, msg_type="image", msg="https://cdn/x.jpg", sequence_id=2)
    m = parse_webhook(body)
    assert not m.is_text and m.text.startswith("https://")


# ---------------- 验签 ---------------- #
def test_signature_roundtrip():
    secret = "topsecret"
    params = {"data": "123", "event": "message", "timestamp": "1739000000000"}
    signable = sorted(f"{k}={v}" for k, v in params.items())
    raw = secret + "&" + "&".join(signable)
    sig = hashlib.md5(raw.encode()).hexdigest()
    assert verify_signature(secret, dict(params, signature=sig))
    assert not verify_signature("wrong", dict(params, signature=sig))
    assert not verify_signature(secret, dict(params, signature="deadbeef"))


def test_signature_requires_both_secret_and_sig():
    assert not verify_signature("", {"signature": "x"})
    assert not verify_signature("s", {"data": "1"})  # 无 signature


def test_debug_signature_shows_both_sides():
    d = debug_signature("s", {"data": "1", "signature": "zzz"})
    assert d["收到"] == "zzz" and len(d["算出"]) == 32 and "s&" in d["签名串"]


# ---------------- 入站处理 ---------------- #
def _fake_extractor(fields):
    return lambda conv, schema: dict(fields)


def test_inbound_creates_lead(db):
    inb = SaleSmartlyInbound(db, _fake_extractor(
        {"pax_count": 2, "ages": "35", "depart_date": "2026-10-05",
         "duration_days": 4, "cities": ["北京"], "has_flight": False, "has_budget": False}))
    out = inb.handle(parse_webhook(SAMPLE))
    assert out["channel"] == "Instagram"
    assert out["status"] == "qualified" and out["lead_id"]
    assert db.get_lead(out["lead_id"]).cities == ["北京"]


def test_inbound_idempotent_on_repeat(db):
    inb = SaleSmartlyInbound(db, _fake_extractor({"pax_count": 2}))
    m = parse_webhook(SAMPLE)
    inb.handle(m)
    again = inb.handle(m)  # 同 sequence_id 重投
    assert again.get("duplicate") is True
    # 只建了一条 lead
    assert db.conn.execute("SELECT COUNT(*) c FROM leads").fetchone()["c"] == 1


def test_multi_turn_updates_same_lead(db):
    # 第一条只给一半信息 → collecting；第二条补齐 → qualified，且是同一条 lead
    calls = iter([
        {"pax_count": 2, "cities": ["北京"]},                       # 第 1 轮：不全
        {"pax_count": 2, "ages": "35", "depart_date": "2026-10-05",  # 第 2 轮：补齐
         "duration_days": 4, "cities": ["北京", "西安"],
         "has_flight": False, "has_budget": False},
    ])
    inb = SaleSmartlyInbound(db, lambda conv, schema: next(calls))
    r1 = inb.handle(parse_webhook(dict(SAMPLE, sequence_id=1, msg="两个人去北京")))
    assert r1["status"] == "collecting"
    r2 = inb.handle(parse_webhook(dict(SAMPLE, sequence_id=2, msg="十月五号，四天，还想去西安")))
    assert r2["status"] == "qualified"
    assert r1["lead_id"] == r2["lead_id"]                    # 同一条 lead 被更新
    assert db.conn.execute("SELECT COUNT(*) c FROM leads").fetchone()["c"] == 1
    assert db.get_lead(r2["lead_id"]).cities == ["北京", "西安"]


def test_non_text_message_skipped_no_lead(db):
    inb = SaleSmartlyInbound(db, _fake_extractor({"pax_count": 2}))
    out = inb.handle(parse_webhook(dict(SAMPLE, msg_type="image", msg="https://cdn/x.jpg")))
    assert out.get("skipped") is True
    assert db.conn.execute("SELECT COUNT(*) c FROM leads").fetchone()["c"] == 0


def test_webhook_path_constant():
    assert WEBHOOK_PATH == "/salesmartly/webhook"
