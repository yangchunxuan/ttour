"""company/channels/wecom/callback.py — 企业微信回调 HTTP 服务(纯 stdlib http.server)。

企业微信会往这个网址推两种请求:
  * GET  ?msg_signature&timestamp&nonce&echostr  —— 配置回调URL时的"验证",要解密 echostr 原样回。
  * POST ?msg_signature&timestamp&nonce  body=加密XML —— 有客服消息/事件时推来,解密拿到 Token/OpenKfId。

设计:POST 先**立刻回 "success"**(企业微信要求 5s 内响应,否则重推),再把事件交给 on_event。
真正的"拉消息(sync_msg)"这种耗时活由 on_event 那边丢后台做,别卡住回调响应。
"""

from __future__ import annotations

import logging
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from company.channels.wecom.crypto import (
    WXBizMsgCrypt, WecomCryptError, parse_callback_event,
)

log = logging.getLogger("wecom.callback")

EventHandler = Callable[[dict], None]   # 收到解析好的事件 dict(含 Token/OpenKfId 等)


def make_handler(crypt: WXBizMsgCrypt, on_event: EventHandler):
    class _Handler(BaseHTTPRequestHandler):
        server_version = "wecom-kf/1.0"

        def _query(self) -> dict:
            q = urllib.parse.urlparse(self.path).query
            return {k: v[0] for k, v in urllib.parse.parse_qs(q).items()}

        def _write(self, status: int, body: bytes = b"", ctype: str = "text/plain; charset=utf-8"):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self):
            p = self._query()
            try:
                echo = crypt.verify_url(
                    p.get("msg_signature", ""), p.get("timestamp", ""),
                    p.get("nonce", ""), p.get("echostr", ""))
            except WecomCryptError as e:
                log.warning("回调URL验证失败: %s", e.code)
                self._write(401, b"invalid signature")
                return
            self._write(200, echo.encode("utf-8"))     # 原样回明文 = 验证通过

        def do_POST(self):
            p = self._query()
            n = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(n).decode("utf-8") if n else ""
            # 无论如何都回 success —— 避免企业微信因非200而重推风暴
            self._write(200, b"success")
            try:
                plain = crypt.decrypt_msg(
                    raw, p.get("msg_signature", ""), p.get("timestamp", ""),
                    p.get("nonce", ""))
                event = parse_callback_event(plain)
            except Exception as e:                       # noqa: BLE001
                log.warning("回调解密/解析失败(已回success): %s", e)
                return
            try:
                on_event(event)
            except Exception:                            # noqa: BLE001
                log.exception("on_event 处理异常")

        def log_message(self, *args):
            return   # 静音 http.server 的默认 stderr 日志

    return _Handler


def make_server(crypt: WXBizMsgCrypt, on_event: EventHandler,
                host: str = "127.0.0.1", port: int = 9000) -> ThreadingHTTPServer:
    """建好(但不启动)回调服务。穿透工具把公网流量转发到 host:port。"""
    return ThreadingHTTPServer((host, port), make_handler(crypt, on_event))
