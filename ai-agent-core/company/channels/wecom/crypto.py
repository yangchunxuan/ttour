"""company/channels/wecom/crypto.py — 企业微信回调消息加解密(WXBizMsgCrypt)。

企业微信回调是"安全模式":GET 验证回调URL、POST 收事件,都要
  1) 用 SHA1(sort(token, timestamp, nonce, encrypt)) 校验 msg_signature;
  2) 对密文做 AES-256-CBC 解密。
本实现严格对齐官方 WXBizMsgCrypt 规范:
  - AESKey = base64decode(EncodingAESKey + "=") ,32 字节;IV = AESKey[:16]。
  - 明文结构 = 16字节随机 + 4字节网络序长度 + 正文 + receiveid(=CorpID);PKCS7 补到 32 的倍数。
依赖 pycryptodome 提供 AES(其余纯 stdlib)。

安全要点:receiveid 必须等于本企业 CorpID,否则拒收(防串消息);签名不符一律拒。
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import struct
import xml.etree.ElementTree as ET

from Crypto.Cipher import AES

_AES_BLOCK = 32  # 企业微信用 PKCS7 补到 32 字节的倍数


class WecomCryptError(Exception):
    """签名/解密/校验失败。带一个短码,便于回调服务分辨该返回什么。"""

    def __init__(self, code: str, msg: str = ""):
        self.code = code
        super().__init__(f"{code}: {msg}")


def sha1_signature(token: str, timestamp: str, nonce: str, encrypt: str) -> str:
    """官方签名:四个串字典序排序后拼接,取 SHA1 十六进制。"""
    items = sorted([token, timestamp, nonce, encrypt])
    return hashlib.sha1("".join(items).encode("utf-8")).hexdigest()


def _pkcs7_pad(text: bytes) -> bytes:
    amount = _AES_BLOCK - (len(text) % _AES_BLOCK)  # 整除时补一整块;恒在 [1,32]
    return text + bytes([amount]) * amount


def _pkcs7_unpad(decrypted: bytes) -> bytes:
    pad = decrypted[-1] if decrypted else 0
    if pad < 1 or pad > _AES_BLOCK:
        raise WecomCryptError("IllegalBuffer", "PKCS7 填充非法")
    return decrypted[:-pad]


class WXBizMsgCrypt:
    def __init__(self, token: str, encoding_aes_key: str, receiveid: str):
        self.token = token
        self.receiveid = receiveid           # 企业微信自建应用回调里 receiveid = CorpID
        try:
            self.key = base64.b64decode(encoding_aes_key + "=")
        except Exception:                    # noqa: BLE001
            raise WecomCryptError("IllegalAesKey", "EncodingAESKey 无法 base64 解码")
        if len(self.key) != 32:
            raise WecomCryptError("IllegalAesKey", "AESKey 必须 32 字节(EncodingAESKey 应为43位)")
        self.iv = self.key[:16]

    # ---- 原语 ---- #
    def _encrypt(self, text: str) -> str:
        raw = text.encode("utf-8")
        msg = (secrets.token_bytes(16)
               + struct.pack(">I", len(raw))
               + raw
               + self.receiveid.encode("utf-8"))
        cipher = AES.new(self.key, AES.MODE_CBC, self.iv)
        return base64.b64encode(cipher.encrypt(_pkcs7_pad(msg))).decode("utf-8")

    def _decrypt(self, encrypt: str) -> str:
        cipher = AES.new(self.key, AES.MODE_CBC, self.iv)
        try:
            plain = _pkcs7_unpad(cipher.decrypt(base64.b64decode(encrypt)))
        except WecomCryptError:
            raise
        except Exception:                    # noqa: BLE001
            raise WecomCryptError("DecryptAES", "AES 解密失败")
        if len(plain) < 20:
            raise WecomCryptError("IllegalBuffer", "解密结果过短")
        msg_len = struct.unpack(">I", plain[16:20])[0]
        if 20 + msg_len > len(plain):
            raise WecomCryptError("IllegalBuffer", "长度字段越界")
        content = plain[20:20 + msg_len]
        receiveid = plain[20 + msg_len:]
        if receiveid.decode("utf-8", "ignore") != self.receiveid:
            raise WecomCryptError("ValidateCorpid", "receiveid 与本企业 CorpID 不符,拒收")
        return content.decode("utf-8")

    # ---- 对外 ---- #
    def verify_url(self, msg_signature: str, timestamp: str, nonce: str,
                   echostr: str) -> str:
        """GET 验回调URL:校签 → 解密 echostr → 返回明文(原样回给企业微信即验证通过)。"""
        if sha1_signature(self.token, timestamp, nonce, echostr) != msg_signature:
            raise WecomCryptError("ValidateSignature", "URL 验证签名不符")
        return self._decrypt(echostr)

    def decrypt_msg(self, post_body: str, msg_signature: str, timestamp: str,
                    nonce: str) -> str:
        """POST 收事件:取 XML 里的 <Encrypt> → 校签 → 解密 → 返回明文 XML。"""
        try:
            encrypt = ET.fromstring(post_body).find("Encrypt").text  # type: ignore[union-attr]
        except Exception:                    # noqa: BLE001
            raise WecomCryptError("ParseXml", "回调 XML 里找不到 <Encrypt>")
        if not encrypt:
            raise WecomCryptError("ParseXml", "<Encrypt> 为空")
        if sha1_signature(self.token, timestamp, nonce, encrypt) != msg_signature:
            raise WecomCryptError("ValidateSignature", "消息签名不符")
        return self._decrypt(encrypt)

    def encrypt_msg(self, reply_xml: str, nonce: str, timestamp: str) -> str:
        """把回复明文加密成企业微信要求的密文 XML(被动回复用;客服多用主动 send_msg,此为备用)。"""
        encrypt = self._encrypt(reply_xml)
        signature = sha1_signature(self.token, timestamp, nonce, encrypt)
        return ("<xml>"
                f"<Encrypt><![CDATA[{encrypt}]]></Encrypt>"
                f"<MsgSignature><![CDATA[{signature}]]></MsgSignature>"
                f"<TimeStamp>{timestamp}</TimeStamp>"
                f"<Nonce><![CDATA[{nonce}]]></Nonce>"
                "</xml>")


def parse_callback_event(xml_content: str) -> dict:
    """从解密后的 XML 抽微信客服事件关键字段。收到 kf_msg_or_event 时 Token/OpenKfId 用来 sync_msg。"""
    root = ET.fromstring(xml_content)

    def txt(tag: str) -> str | None:
        el = root.find(tag)
        return el.text if el is not None else None

    return {
        "ToUserName": txt("ToUserName"),
        "CreateTime": txt("CreateTime"),
        "MsgType": txt("MsgType"),
        "Event": txt("Event"),
        "Token": txt("Token"),
        "OpenKfId": txt("OpenKfId"),
    }
