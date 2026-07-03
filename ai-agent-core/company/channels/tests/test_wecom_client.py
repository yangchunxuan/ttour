"""企业微信客户端测试:离线、注入假 HTTP。重点验证 token 缓存、失效重试、错误必抛。"""

from __future__ import annotations

import contextlib
import io
import json

import pytest

from company.channels.wecom import client as C
from company.channels.wecom.client import WecomKfClient, WecomError


class _FakeResp(io.BytesIO):
    """让 BytesIO 支持 with urlopen(...) as r 的上下文用法。"""
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _queue_responses(monkeypatch, responses: list[dict]):
    """按顺序返回 responses 里的 JSON;记录每次请求的 (url, body)。"""
    calls: list[tuple] = []
    seq = iter(responses)

    def fake_urlopen(req, timeout=None):
        if hasattr(req, "full_url"):  # POST(Request 对象)
            url = req.full_url
            body = req.data.decode("utf-8") if req.data else ""
        else:                         # GET(str)
            url = req
            body = ""
        calls.append((url, body))
        return _FakeResp(json.dumps(next(seq)).encode("utf-8"))

    monkeypatch.setattr(C.urllib.request, "urlopen", fake_urlopen)
    return calls


def test_token_cached(monkeypatch):
    calls = _queue_responses(monkeypatch, [
        {"errcode": 0, "access_token": "TOK1", "expires_in": 7200},
    ])
    c = WecomKfClient("ww1", "sec")
    assert c.token() == "TOK1"
    assert c.token() == "TOK1"          # 第二次走缓存,不再发请求
    assert len(calls) == 1
    assert "gettoken" in calls[0][0]


def test_token_error_raises(monkeypatch):
    _queue_responses(monkeypatch, [{"errcode": 40001, "errmsg": "invalid secret"}])
    c = WecomKfClient("ww1", "bad")
    with pytest.raises(WecomError) as e:
        c.token()
    assert e.value.errcode == 40001


def test_call_refreshes_on_expired_token(monkeypatch):
    calls = _queue_responses(monkeypatch, [
        {"errcode": 0, "access_token": "TOK1", "expires_in": 7200},   # 首次 gettoken
        {"errcode": 42001, "errmsg": "access_token expired"},          # 业务调用:过期
        {"errcode": 0, "access_token": "TOK2", "expires_in": 7200},   # 强制刷新 token
        {"errcode": 0, "account_list": [{"open_kfid": "kf1"}]},        # 重试成功
    ])
    c = WecomKfClient("ww1", "sec")
    accounts = c.kf_account_list()
    assert accounts == [{"open_kfid": "kf1"}]
    # 第 3 次请求应是强制刷新 token
    assert "gettoken" in calls[2][0]


def test_business_error_raises_with_code(monkeypatch):
    _queue_responses(monkeypatch, [
        {"errcode": 0, "access_token": "TOK1", "expires_in": 7200},
        {"errcode": 60020, "errmsg": "not allow to access from your ip"},
    ])
    c = WecomKfClient("ww1", "sec")
    with pytest.raises(WecomError) as e:
        c.kf_account_list()
    assert e.value.errcode == 60020        # 上层可据此提示"去加可信IP"


def test_sync_msg_body_omits_empty(monkeypatch):
    calls = _queue_responses(monkeypatch, [
        {"errcode": 0, "access_token": "TOK1", "expires_in": 7200},
        {"errcode": 0, "msg_list": [], "next_cursor": "c2", "has_more": 0},
    ])
    c = WecomKfClient("ww1", "sec")
    c.sync_msg(cursor="c1", token="tk")
    body = json.loads(calls[1][1])
    assert body["cursor"] == "c1" and body["token"] == "tk"
    assert "open_kfid" not in body        # 空值不塞进 body


def test_send_text_shape(monkeypatch):
    calls = _queue_responses(monkeypatch, [
        {"errcode": 0, "access_token": "TOK1", "expires_in": 7200},
        {"errcode": 0, "msgid": "m1"},
    ])
    c = WecomKfClient("ww1", "sec")
    c.send_text(touser="ext1", open_kfid="kf1", content="您好")
    body = json.loads(calls[1][1])
    assert body["msgtype"] == "text" and body["text"]["content"] == "您好"
    assert body["touser"] == "ext1" and body["open_kfid"] == "kf1"
