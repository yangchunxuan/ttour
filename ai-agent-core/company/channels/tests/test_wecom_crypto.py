"""WXBizMsgCrypt 测试:往返一致 + 签名校验 + 防串企业 + 篡改必拒。

注意:要用装了 pycryptodome 的 venv 跑:  .venv/bin/python -m pytest 这个文件
"""

from __future__ import annotations

import base64
import secrets

import pytest

from company.channels.wecom.crypto import (
    WXBizMsgCrypt, WecomCryptError, sha1_signature, parse_callback_event,
)

CORPID = "ww13a25bee0640c1aa"
TOKEN = "mytoken123"


def _aeskey() -> str:
    """造一个合法的 43 位 EncodingAESKey(base64(32字节) 去掉尾部 '=')。"""
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")[:43]


def test_encrypt_decrypt_roundtrip():
    c = WXBizMsgCrypt(TOKEN, _aeskey(), CORPID)
    for text in ["<xml>hello</xml>", "你好世界 🏔️", "a" * 5000, ""]:
        assert c._decrypt(c._encrypt(text)) == text


def test_verify_url_ok_and_tamper_rejected():
    c = WXBizMsgCrypt(TOKEN, _aeskey(), CORPID)
    echo = c._encrypt("2379745941957含中文echostr")
    sig = sha1_signature(TOKEN, "1700000000", "nonceX", echo)
    assert c.verify_url(sig, "1700000000", "nonceX", echo) == "2379745941957含中文echostr"
    with pytest.raises(WecomCryptError):
        c.verify_url("deadbeef", "1700000000", "nonceX", echo)      # 签名错


def test_decrypt_msg_from_xml():
    c = WXBizMsgCrypt(TOKEN, _aeskey(), CORPID)
    inner = ("<xml><ToUserName>ww1</ToUserName><MsgType>event</MsgType>"
             "<Event>kf_msg_or_event</Event><Token>ENQAB123</Token>"
             "<OpenKfId>wkAB</OpenKfId></xml>")
    enc = c._encrypt(inner)
    body = f"<xml><ToUserName>ww1</ToUserName><Encrypt><![CDATA[{enc}]]></Encrypt></xml>"
    sig = sha1_signature(TOKEN, "1700000001", "n2", enc)
    plain = c.decrypt_msg(body, sig, "1700000001", "n2")
    ev = parse_callback_event(plain)
    assert ev["Token"] == "ENQAB123" and ev["OpenKfId"] == "wkAB"
    assert ev["Event"] == "kf_msg_or_event"


def test_wrong_corpid_rejected():
    key = _aeskey()
    enc = WXBizMsgCrypt(TOKEN, key, "corpAAAAAAAAAAAA")._encrypt("secret")
    other = WXBizMsgCrypt(TOKEN, key, "corpBBBBBBBBBBBB")   # 同 key 不同企业
    with pytest.raises(WecomCryptError) as e:
        other._decrypt(enc)
    assert e.value.code == "ValidateCorpid"


def test_bad_aeskey_rejected():
    with pytest.raises(WecomCryptError):
        WXBizMsgCrypt(TOKEN, "tooshort", CORPID)


def test_missing_encrypt_node():
    c = WXBizMsgCrypt(TOKEN, _aeskey(), CORPID)
    with pytest.raises(WecomCryptError):
        c.decrypt_msg("<xml><NoEncrypt/></xml>", "sig", "ts", "n")


def test_signature_is_order_independent():
    # 官方签名对四元组排序,与传入顺序无关
    a = sha1_signature("t", "111", "nnn", "eee")
    b = sha1_signature("t", "eee", "111", "nnn")   # 打乱(仍是同一集合)
    assert a == b
