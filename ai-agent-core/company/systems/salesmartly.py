"""company/systems/salesmartly.py —— 真·SaleSmartly 前门（客户从这里进公司）。

自动公司的**真入口**：Facebook / Instagram / … 的客户消息 → SaleSmartly → 实时
POST（webhook）到本服务 → 验签 → 归一化 → 客服 agent 抽需求 → 落库成 Lead。
这一段是照 SaleSmartly 官方 webhook 文档做的**真实现**（不是占位/猜测）。

────────────────────────────────────────────────────────────────────────
入站 webhook 契约（来自官方文档，已核对）：
  event = "message"                         # 只处理这个事件
  chat_user_id   (String)  → 客户(contact)
  chat_session_id(String)  → 会话(conversation)
  sequence_id    (Int)     → 消息号（幂等去重用）
  msg                       → 内容（文本类=字符串；图片/文件=URL；系统消息=对象）
  msg_type ∈ {text, button, image, file, system_message, email}
  channel  (Int)：1 Messenger / 2 网页插件 / 3 Email / 5 Instagram / 6 Line /
                  7 WhatsApp API / 12 WhatsApp App / 15 Telegram / 16 TikTok
  send_time (String)        → 毫秒时间戳
验签：回调 URL 带 timestamp、signature 两个查询参数；
  signature = md5( secret 与其余参数按名字母序拼成 key=value 后用 & 连接 )
  官方示例：md5("secret&data=123&event=message")
────────────────────────────────────────────────────────────────────────
上线只差你两样（**运行时输入，我造不出来**）：
  1) 在 SaleSmartly 后台（Max/企业版套餐）把「新消息」webhook 指到
     本服务的  https://<你的公网域名>/salesmartly/webhook
  2) 把该 webhook 的**签名 secret** 给我（放环境变量 SS_WEBHOOK_SECRET，验签用）
出站（自动回客户/分配客服）走 REST API，另需 Max 套餐 API token —— 见
  SaleSmartlyClient，其 endpoint 路径/鉴权头以你套餐的 apifox 文档为准（已标 CONFIRM）。

跑起来（本机验证用，不接真 LLM 也能起）：
  python3 -m company.systems.salesmartly serve --port 8899 --no-verify
真上线：设 SS_WEBHOOK_SECRET + BROKER_BASE_URL/BROKER_TOKEN 后去掉 --no-verify。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from company.record.db import Database
from company.roles.customer_service import CustomerServiceAgent, Extractor, make_llm_extractor


# 官方 channel 编码 → 人类可读名（webhook 文档）
CHANNELS = {
    1: "Messenger", 2: "网页插件", 3: "Email", 5: "Instagram", 6: "Line",
    7: "WhatsApp API", 12: "WhatsApp App", 15: "Telegram", 16: "TikTok",
}

# channel → 投放平台归一标签（漏斗/投放优化按此维度统计，跨组件一致）
CHANNEL_PLATFORM = {
    1: "facebook", 2: "web", 3: "email", 5: "instagram", 6: "line",
    7: "whatsapp", 12: "whatsapp", 15: "telegram", 16: "tiktok",
}

WEBHOOK_PATH = "/salesmartly/webhook"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------ #
# 1) 归一化：webhook 载荷 → InboundMessage
# ------------------------------------------------------------------ #
@dataclass
class InboundMessage:
    conversation_id: str   # chat_session_id
    contact_id: str        # chat_user_id
    message_id: int        # sequence_id
    text: str              # msg（文本类为原文；非文本存其 URL/JSON 备档）
    msg_type: str          # text/button/image/file/system_message/email
    channel: int
    send_time: str         # 毫秒时间戳

    @property
    def channel_name(self) -> str:
        return CHANNELS.get(self.channel, f"channel-{self.channel}")

    @property
    def platform(self) -> str:
        """投放平台归一标签（facebook/instagram/…），漏斗按此统计。"""
        return CHANNEL_PLATFORM.get(self.channel, self.channel_name)

    @property
    def is_text(self) -> bool:
        # 文本与按钮回复算需求线索；图片/文件/系统消息只备档不参与抽取
        return self.msg_type in ("text", "button")


def parse_webhook(body: dict) -> Optional[InboundMessage]:
    """把 SaleSmartly 'message' 事件解析成 InboundMessage；非消息事件返回 None。"""
    if not isinstance(body, dict) or body.get("event") != "message":
        return None
    msg = body.get("msg")
    text = msg if isinstance(msg, str) else json.dumps(msg, ensure_ascii=False)
    return InboundMessage(
        conversation_id=str(body.get("chat_session_id", "")),
        contact_id=str(body.get("chat_user_id", "")),
        message_id=int(body.get("sequence_id", 0) or 0),
        text=text,
        msg_type=str(body.get("msg_type", "")),
        channel=int(body.get("channel", 0) or 0),
        send_time=str(body.get("send_time", "")),
    )


def verify_signature(secret: str, params: dict) -> bool:
    """验签：md5(secret & 其余查询参数按名字母序拼 key=value)。
    params = 回调 URL 的查询串（含 signature、timestamp 等）。
    官方示例：md5("secret&data=123&event=message")。

    注：官方文档只给了 data/event 两参数的示例；这里对**除 signature 外的全部
    查询参数**按字母序参与签名（最保守的通用实现）。首次接真回调若比对不上，
    用 debug_signature() 打印「收到 vs 算出」即可锁定确切参与项。
    """
    got = str(params.get("signature", ""))
    if not (secret and got):
        return False
    signable = sorted((k, str(v)) for k, v in params.items() if k != "signature")
    payload = "&".join(f"{k}={v}" for k, v in signable)
    raw = f"{secret}&{payload}" if payload else secret
    return hashlib.md5(raw.encode("utf-8")).hexdigest() == got


def debug_signature(secret: str, params: dict) -> dict:
    """首次对接排签名用：返回 {收到, 算出, 签名串}，方便肉眼锁定参与项。"""
    signable = sorted((k, str(v)) for k, v in params.items() if k != "signature")
    payload = "&".join(f"{k}={v}" for k, v in signable)
    raw = f"{secret}&{payload}" if payload else secret
    return {"收到": str(params.get("signature", "")),
            "算出": hashlib.md5(raw.encode("utf-8")).hexdigest(),
            "签名串": raw}


# ------------------------------------------------------------------ #
# 2) 入站处理：累积会话 → 客服抽需求 → 落 Lead（幂等、随消息渐次补全）
#    适配器自带存储（不污染核心 schema.sql）。
# ------------------------------------------------------------------ #
_INBOUND_DDL = """
CREATE TABLE IF NOT EXISTS ss_sessions(
  chat_session_id TEXT PRIMARY KEY,
  chat_user_id    TEXT,
  channel         INTEGER,
  transcript      TEXT NOT NULL DEFAULT '',
  lead_id         INTEGER,
  updated_at      TEXT
);
CREATE TABLE IF NOT EXISTS ss_messages(
  chat_session_id TEXT,
  sequence_id     INTEGER,
  msg_type        TEXT,
  text            TEXT,
  send_time       TEXT,
  PRIMARY KEY(chat_session_id, sequence_id)
);
"""


class SaleSmartlyInbound:
    """SaleSmartly 进来的每条消息 → 累积会话 → 客服 agent 抽需求 → 建/更新 Lead。"""

    def __init__(self, db: Database, extractor: Extractor):
        self.db = db
        self.cs = CustomerServiceAgent(db, extractor)
        from company.roles.analytics import AnalyticsAgent
        self.an = AnalyticsAgent(db)
        db.conn.executescript(_INBOUND_DDL)
        db.conn.commit()

    def handle(self, m: InboundMessage) -> dict:
        conn = self.db.conn
        # 幂等：同一 (会话, 消息号) 只处理一次（SaleSmartly 可能重投）
        if conn.execute(
            "SELECT 1 FROM ss_messages WHERE chat_session_id=? AND sequence_id=?",
            (m.conversation_id, m.message_id),
        ).fetchone():
            return {"session": m.conversation_id, "duplicate": True}
        conn.execute(
            "INSERT INTO ss_messages(chat_session_id,sequence_id,msg_type,text,send_time) "
            "VALUES(?,?,?,?,?)",
            (m.conversation_id, m.message_id, m.msg_type, m.text, m.send_time),
        )

        row = conn.execute(
            "SELECT transcript, lead_id FROM ss_sessions WHERE chat_session_id=?",
            (m.conversation_id,),
        ).fetchone()
        is_new = row is None    # 新会话 = 一次「咨询」（V2 一·3 咨询人数）
        if row is None:
            conn.execute(
                "INSERT INTO ss_sessions(chat_session_id,chat_user_id,channel,transcript,updated_at)"
                " VALUES(?,?,?,?,?)",
                (m.conversation_id, m.contact_id, m.channel, "", _now()),
            )
            transcript, lead_id = "", None
        else:
            transcript, lead_id = row["transcript"], row["lead_id"]

        if m.is_text and m.text.strip():
            transcript = (transcript + "\n客户：" + m.text.strip()).strip()
        conn.execute(
            "UPDATE ss_sessions SET transcript=?, updated_at=? WHERE chat_session_id=?",
            (transcript, _now(), m.conversation_id),
        )
        conn.commit()

        # 非文本消息（图片/系统消息等）只备档，不触发需求抽取
        if not (m.is_text and transcript):
            if is_new:   # 客户首次接触即记一次咨询
                self.an.record_event("inquiry", lead_id=None, platform=m.platform)
            return {"session": m.conversation_id, "contact": m.contact_id,
                    "channel": m.channel_name, "msg_type": m.msg_type, "skipped": True}

        # 整段 transcript 重抽 → 字段随对话渐次填满（同一 Lead 更新，不新建）
        result = self.cs.ingest(transcript, source=f"salesmartly:{m.channel_name}",
                                lead_id=lead_id, platform=m.platform)
        conn.execute("UPDATE ss_sessions SET lead_id=? WHERE chat_session_id=?",
                     (result["lead_id"], m.conversation_id))
        conn.commit()
        if is_new:   # 每个会话只记一次咨询（inquiry），归一化平台标签便于漏斗统计
            self.an.record_event("inquiry", lead_id=result["lead_id"], platform=m.platform)
        return {"session": m.conversation_id, "contact": m.contact_id,
                "channel": m.channel_name, "platform": m.platform, **result}


# ------------------------------------------------------------------ #
# 3) 出站 REST 客户端（自动回客户 / 分配客服）
#    结构真实（token 鉴权 + JSON POST），但**精确 endpoint 路径与鉴权头**
#    在 Max 套餐的 apifox 文档里（登录墙后），下面标 CONFIRM 的地方以你的文档为准。
#    §3 安全：给客户/供应商发消息=对外承诺，默认经人审再发（见 pipeline 闸门）。
# ------------------------------------------------------------------ #
class SaleSmartlyClient:
    """SaleSmartly 出站 API 薄客户端（stdlib，无三方依赖）。"""

    # CONFIRM：路径以你 Max 套餐 apifox 文档为准（https://salesmartly-api.apifox.cn）
    ENDPOINTS = {
        "send_text": "/api/v1/message/send",     # CONFIRM
        "assign":    "/api/v1/session/assign",   # CONFIRM
    }

    def __init__(self, api_token: str, base_url: str = "https://api.salesmartly.com",
                 auth_header: str = "Authorization", auth_scheme: str = "Bearer"):
        # CONFIRM：base_url / auth_header / auth_scheme 以你的 apifox 文档为准
        self.api_token = api_token
        self.base_url = base_url.rstrip("/")
        self.auth_header = auth_header
        self.auth_scheme = auth_scheme

    def _post(self, path: str, payload: dict) -> dict:
        import urllib.request
        url = self.base_url + path
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        token = f"{self.auth_scheme} {self.api_token}".strip()
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json", self.auth_header: token},
        )
        with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310（自有 base_url）
            return json.loads(r.read().decode("utf-8") or "{}")

    def send_text(self, chat_session_id: str, text: str) -> dict:
        """给某会话回一条文本。真发消息=对外承诺，调用方须已过人审闸门。"""
        return self._post(self.ENDPOINTS["send_text"],
                          {"chat_session_id": chat_session_id, "msg_type": "text", "msg": text})

    def assign(self, chat_session_id: str, member_id: str) -> dict:
        """把会话分配给某客服/成员。"""
        return self._post(self.ENDPOINTS["assign"],
                          {"chat_session_id": chat_session_id, "member_id": member_id})


# ------------------------------------------------------------------ #
# 4) 可运行的 webhook 服务（stdlib http.server，无三方依赖）
# ------------------------------------------------------------------ #
def build_server(db: Database, extractor: Extractor, secret: Optional[str] = None,
                 host: str = "127.0.0.1", port: int = 8899, verify: bool = True):
    """起一个真实的 webhook HTTP 服务，返回 (httpd, inbound)。调用方 serve_forever。"""
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import urlparse, parse_qs

    inbound = SaleSmartlyInbound(db, extractor)

    class Handler(BaseHTTPRequestHandler):
        def _json(self, code: int, obj: dict) -> None:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))

        def do_POST(self):  # noqa: N802
            u = urlparse(self.path)
            if u.path != WEBHOOK_PATH:
                return self._json(404, {"error": "not found"})
            params = {k: v[0] for k, v in parse_qs(u.query).items()}
            if verify:
                if not secret or not verify_signature(secret, params):
                    return self._json(401, {"error": "bad signature"})
            n = int(self.headers.get("Content-Length", 0) or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                return self._json(400, {"error": "bad json"})
            m = parse_webhook(body)
            if m is None:
                return self._json(200, {"ok": True, "ignored": True})
            return self._json(200, inbound.handle(m))

        def log_message(self, *a):  # 静音默认访问日志
            pass

    httpd = HTTPServer((host, port), Handler)
    return httpd, inbound


def _extractor_from_env() -> Extractor:
    """有 broker 环境变量 → 真 LLM 抽取；没有 → 直通桩（仅本机验证流程用）。"""
    base = os.environ.get("BROKER_BASE_URL")
    token = os.environ.get("BROKER_TOKEN")
    if base and token:
        return make_llm_extractor(base, token,
                                  os.environ.get("BROKER_MODEL", "deepseek-v4-flash"))

    def _stub(conversation_text: str, schema: str) -> dict:
        # 无 LLM 时的最小直通：只把整段对话存进 convo_summary，字段留空（status=collecting）
        return {"special_requests": conversation_text[-500:]}
    return _stub


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="SaleSmartly webhook 前门")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ps = sub.add_parser("serve", help="起 webhook 服务")
    ps.add_argument("--host", default="127.0.0.1")
    ps.add_argument("--port", type=int, default=8899)
    ps.add_argument("--no-verify", action="store_true",
                    help="跳过验签（本机验证用；上线务必去掉并设 SS_WEBHOOK_SECRET）")
    args = ap.parse_args(argv)

    if args.cmd == "serve":
        db = Database(check_same_thread=False)  # 服务可能在独立线程处理请求
        secret = os.environ.get("SS_WEBHOOK_SECRET")
        verify = not args.no_verify
        httpd, _ = build_server(db, _extractor_from_env(), secret=secret,
                                host=args.host, port=args.port, verify=verify)
        print(f"[SaleSmartly] 监听 http://{args.host}:{args.port}{WEBHOOK_PATH}")
        print(f"[SaleSmartly] 验签={'开' if verify else '关'}  "
              f"secret={'已设' if secret else '未设'}  "
              f"抽取={'LLM' if os.environ.get('BROKER_BASE_URL') else '桩(无LLM)'}")
        print("[SaleSmartly] 到 SaleSmartly 后台把「新消息」webhook 指到本地址（需公网可达/内网穿透）")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[SaleSmartly] 停")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
