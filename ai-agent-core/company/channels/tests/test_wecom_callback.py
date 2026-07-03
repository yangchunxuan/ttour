"""回调服务测试:真起一个 http 服务,模拟企业微信的 GET验证 / POST推事件。

要用装了 pycryptodome 的 venv 跑:  .venv/bin/python -m pytest 这个文件
"""

from __future__ import annotations

import base64
import secrets
import threading
import time
import urllib.request

import pytest

from company.channels.wecom.callback import make_server
from company.channels.wecom.crypto import WXBizMsgCrypt, sha1_signature

CORPID = "ww13a25bee0640c1aa"
TOKEN = "cbtoken"


def _aeskey() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")[:43]


@pytest.fixture()
def running():
    key = _aeskey()
    crypt = WXBizMsgCrypt(TOKEN, key, CORPID)
    events: list[dict] = []
    srv = make_server(crypt, events.append, host="127.0.0.1", port=0)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    time.sleep(0.05)
    try:
        yield crypt, port, events
    finally:
        srv.shutdown()


def _get(port, qs):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/?{qs}", timeout=5) as r:
        return r.status, r.read().decode("utf-8")


def _post(port, qs, body):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/?{qs}", data=body.encode("utf-8"), method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, r.read().decode("utf-8")


def test_verify_url_echoes_plaintext(running):
    crypt, port, _ = running
    echo_plain = "5927782489442"
    enc = crypt._encrypt(echo_plain)
    ts, nonce = "1700000000", "nonceA"
    sig = sha1_signature(TOKEN, ts, nonce, enc)
    qs = f"msg_signature={sig}&timestamp={ts}&nonce={nonce}&echostr={urllib.request.quote(enc)}"
    status, body = _get(port, qs)
    assert status == 200 and body == echo_plain      # 原样回明文 = 验证通过


def test_bad_signature_rejected(running):
    crypt, port, _ = running
    enc = crypt._encrypt("x")
    qs = f"msg_signature=bad&timestamp=1&nonce=n&echostr={urllib.request.quote(enc)}"
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(port, qs)
    assert e.value.code == 401


def test_post_event_dispatched(running):
    crypt, port, events = running
    inner = ("<xml><MsgType>event</MsgType><Event>kf_msg_or_event</Event>"
             "<Token>ENC_TOKEN_9</Token><OpenKfId>wkKF01</OpenKfId></xml>")
    enc = crypt._encrypt(inner)
    ts, nonce = "1700000002", "nonceB"
    sig = sha1_signature(TOKEN, ts, nonce, enc)
    body = f"<xml><Encrypt><![CDATA[{enc}]]></Encrypt></xml>"
    status, resp = _post(port, f"msg_signature={sig}&timestamp={ts}&nonce={nonce}", body)
    assert status == 200 and resp == "success"
    time.sleep(0.1)                                    # on_event 在回响应后执行
    assert len(events) == 1
    assert events[0]["Token"] == "ENC_TOKEN_9"
    assert events[0]["OpenKfId"] == "wkKF01"


def test_post_garbage_still_returns_success(running):
    _, port, events = running
    status, resp = _post(port, "msg_signature=x&timestamp=1&nonce=n", "<xml>not encrypted</xml>")
    assert status == 200 and resp == "success"         # 不因坏数据触发企业微信重推风暴
    time.sleep(0.05)
    assert events == []
