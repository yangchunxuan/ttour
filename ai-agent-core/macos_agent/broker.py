"""broker.py — 宿主 Mac 上的密钥中转 broker（spec §2A.3）。

在**宿主 Mac** 上跑，持真 DeepSeek key，把 VM guest 的 chat 请求转发到
api.deepseek.com。**真 key 永不进 VM。**

硬要求（spec §2A.3，逐条实现）：
  1. 绑 host-only/私有网卡的单一接口，只允许该 VM 的 IP；禁止绑桥接/LAN
     可见接口。（BROKER_BIND 默认 127.0.0.1 仅本机；VM 走 host-only 时把
     BROKER_BIND 设成该网卡的宿主地址，BROKER_ALLOW_IPS 设成 guest IP。）
  2. guest↔broker 共享 bearer token（BROKER_TOKEN，每次运行轮换）。
  3. 严格请求 schema：只接受合法 DeepSeek chat 调用，钉死 endpoint、
     model 白名单、封顶 max_tokens、忽略客户端自带 URL。
  4. 每分钟 + 每次运行的限速与花费上限，硬熔断。
  5. 不记录请求/响应 body；只记"发生了转发 + 用量"。
  6. 用专用、可吊销、低配额的 DeepSeek key（BROKER_UPSTREAM_KEY）。

实现细节（spec §2A.3，别踩）：
  * openai SDK 把 /chat/completions 拼到 base_url 后 → VM 端 base_url 给
    http://<host>:<port>/v1，本 broker 只认路径 /v1/chat/completions。
  * ThreadingHTTPServer（单代理会并发 decide+extract，单线程会死锁）。
  * 原样透传上游状态码（否则 SDK 重试逻辑会怪异）。
  * 非流式简单转发；若将来引入流式必须改 chunked 透传。

用法（宿主）：
    export BROKER_UPSTREAM_KEY=sk-...        # 专用可吊销低配额 key
    export BROKER_TOKEN=$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')
    export BROKER_ALLOW_IPS=192.168.66.2     # guest 的 host-only IP（逗号分隔）
    python3 broker.py
VM 端 Planner(base_url="http://<宿主host-only地址>:8899/v1", api_key=$BROKER_TOKEN)
"""

from __future__ import annotations

import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock

# ---- 配置（全部来自环境变量，无默认真 key） ----
UPSTREAM_URL = "https://api.deepseek.com/chat/completions"
UPSTREAM_KEY = os.getenv("BROKER_UPSTREAM_KEY", "")
BROKER_TOKEN = os.getenv("BROKER_TOKEN", "")
BIND_HOST = os.getenv("BROKER_BIND", "127.0.0.1")
BIND_PORT = int(os.getenv("BROKER_PORT", "8899"))
# 只放行这些源 IP（host-only guest 地址）；空 = 只信回环。
ALLOW_IPS = tuple(
    ip.strip() for ip in os.getenv("BROKER_ALLOW_IPS", "").split(",") if ip.strip()
)

# 请求 schema 约束（spec §2A.3.3）
ALLOWED_MODELS = frozenset(
    m.strip() for m in os.getenv(
        "BROKER_ALLOWED_MODELS", "deepseek-v4-flash,deepseek-v4-pro"
    ).split(",") if m.strip()
)
MAX_TOKENS_CAP = int(os.getenv("BROKER_MAX_TOKENS_CAP", "8192"))
UPSTREAM_TIMEOUT = int(os.getenv("BROKER_UPSTREAM_TIMEOUT", "90"))

# 限额（spec §2A.3.4）：硬熔断
RATE_PER_MIN = int(os.getenv("BROKER_RATE_PER_MIN", "40"))
RUN_CALL_CAP = int(os.getenv("BROKER_RUN_CALL_CAP", "400"))
RUN_TOKEN_CAP = int(os.getenv("BROKER_RUN_TOKEN_CAP", "3000000"))


class _Meter:
    """限速 + 每运行花费上限。线程安全（ThreadingHTTPServer 并发）。"""

    def __init__(self) -> None:
        self._lock = Lock()
        self._minute_bucket_start = 0.0
        self._minute_count = 0
        self._run_calls = 0
        self._run_tokens = 0

    def check_and_reserve(self, now: float) -> str:
        """预约一次调用配额。"" = 放行；非空 = 熔断理由。"""
        with self._lock:
            if now - self._minute_bucket_start >= 60.0:
                self._minute_bucket_start = now
                self._minute_count = 0
            if self._minute_count >= RATE_PER_MIN:
                return f"rate limit: >{RATE_PER_MIN} calls/min"
            if self._run_calls >= RUN_CALL_CAP:
                return f"run cap: >{RUN_CALL_CAP} calls this run"
            if self._run_tokens >= RUN_TOKEN_CAP:
                return f"run cap: >{RUN_TOKEN_CAP} tokens this run"
            self._minute_count += 1
            self._run_calls += 1
            return ""

    def add_tokens(self, n: int) -> None:
        with self._lock:
            self._run_tokens += int(n or 0)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "run_calls": self._run_calls,
                "run_tokens": self._run_tokens,
            }


_meter = _Meter()


def _log(event: str, **fields) -> None:
    """只记元数据 + 用量，**绝不记请求/响应 body**（spec §2A.3.5）。"""
    rec = {"ts": round(time.time(), 3), "event": event, **fields}
    sys.stderr.write("[broker] " + json.dumps(rec, ensure_ascii=False) + "\n")
    sys.stderr.flush()


class BrokerHandler(BaseHTTPRequestHandler):
    server_version = "macos-agent-broker/1.0"

    # 静默默认访问日志（会带 path，且我们自记结构化日志）
    def log_message(self, *args) -> None:  # noqa: D401
        return

    def _send_json(self, status: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _client_ip(self) -> str:
        return self.client_address[0] if self.client_address else ""

    def _authorized(self) -> bool:
        # 源 IP 白名单（spec §2A.3.1）
        ip = self._client_ip()
        if ALLOW_IPS:
            if ip not in ALLOW_IPS:
                return False
        elif ip not in ("127.0.0.1", "::1"):
            # 未配 ALLOW_IPS 时只信回环，绝不裸放
            return False
        # bearer token（spec §2A.3.2）——constant-time 比较，不给持钥 broker
        # 留计时侧信道（IP 白名单 + 每运行轮换已兜底，这里再收紧一层）。
        auth = self.headers.get("Authorization", "")
        expected = f"Bearer {BROKER_TOKEN}"
        return bool(BROKER_TOKEN) and hmac.compare_digest(auth, expected)

    def do_GET(self) -> None:  # 健康检查（不需要 token，但仍限源 IP）
        if self.path.rstrip("/") == "/healthz":
            ip = self._client_ip()
            ok_ip = (ip in ALLOW_IPS) if ALLOW_IPS else (ip in ("127.0.0.1", "::1"))
            if not ok_ip:
                self._send_json(403, {"error": "forbidden source"})
                return
            self._send_json(200, {"ok": True, **_meter.snapshot()})
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        # 只认这一个 endpoint（spec §2A.3.3：忽略客户端自带 URL）
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._send_json(404, {"error": "only /v1/chat/completions is served"})
            return

        if not self._authorized():
            _log("reject_auth", ip=self._client_ip())
            self._send_json(401, {"error": "unauthorized"})
            return

        # 读 body（有大小上限，防被塞爆）
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 2_000_000:
            self._send_json(400, {"error": "bad or missing Content-Length"})
            return
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
        except Exception:
            self._send_json(400, {"error": "body is not valid JSON"})
            return

        # ---- 严格 schema 收紧（spec §2A.3.3） ----
        sanitized, reason = self._sanitize(payload)
        if reason:
            _log("reject_schema", ip=self._client_ip(), reason=reason)
            self._send_json(400, {"error": f"rejected: {reason}"})
            return

        # ---- 限额硬熔断（spec §2A.3.4） ----
        breaker = _meter.check_and_reserve(time.time())
        if breaker:
            _log("reject_rate", ip=self._client_ip(), reason=breaker)
            self._send_json(429, {"error": breaker})
            return

        # ---- 转发（原样透传上游状态码；不记 body） ----
        status, resp_bytes, usage_tokens = self._forward(sanitized)
        _meter.add_tokens(usage_tokens)
        _log("forward", ip=self._client_ip(), upstream_status=status,
             tokens=usage_tokens, **_meter.snapshot())

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp_bytes)))
        self.end_headers()
        self.wfile.write(resp_bytes)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _sanitize(payload) -> tuple[dict, str]:
        """只保留合法 DeepSeek chat 字段，钉死 model/endpoint、封顶 max_tokens。"""
        if not isinstance(payload, dict):
            return {}, "payload must be a JSON object"
        model = payload.get("model")
        if model not in ALLOWED_MODELS:
            return {}, f"model {model!r} not in allowlist {sorted(ALLOWED_MODELS)}"
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            return {}, "messages must be a non-empty array"
        if payload.get("stream"):
            return {}, "streaming is not supported by this broker"

        out: dict = {"model": model, "messages": messages}
        # 只放行已知安全字段；封顶 max_tokens
        rf = payload.get("response_format")
        if isinstance(rf, dict) and rf.get("type") in ("json_object", "text"):
            out["response_format"] = rf
        temp = payload.get("temperature")
        if isinstance(temp, (int, float)):
            out["temperature"] = max(0.0, min(float(temp), 2.0))
        mt = payload.get("max_tokens")
        try:
            mt = int(mt) if mt is not None else MAX_TOKENS_CAP
        except (TypeError, ValueError):
            mt = MAX_TOKENS_CAP
        out["max_tokens"] = max(1, min(mt, MAX_TOKENS_CAP))
        return out, ""

    @staticmethod
    def _forward(sanitized: dict) -> tuple[int, bytes, int]:
        req = urllib.request.Request(
            UPSTREAM_URL,
            data=json.dumps(sanitized).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {UPSTREAM_KEY}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT) as resp:
                body = resp.read()
                status = resp.getcode()
        except urllib.error.HTTPError as e:
            # 原样透传上游状态码与错误 body（SDK 依赖它做重试判断）
            body = e.read() if hasattr(e, "read") else b"{}"
            status = e.code
        except Exception as e:  # noqa: BLE001
            return 502, json.dumps({"error": f"broker upstream error: {e}"}).encode(), 0

        # 从响应里抠 usage.total_tokens 计入花费上限（不记完整 body）
        tokens = 0
        try:
            parsed = json.loads(body)
            tokens = int(parsed.get("usage", {}).get("total_tokens", 0))
        except Exception:
            tokens = 0
        return status, body, tokens


def main() -> int:
    if not UPSTREAM_KEY:
        sys.stderr.write(
            "[broker] FATAL: BROKER_UPSTREAM_KEY is empty. "
            "Set a dedicated, revocable, low-quota DeepSeek key (spec §2A.3.6).\n"
        )
        return 2
    if not BROKER_TOKEN:
        sys.stderr.write(
            "[broker] FATAL: BROKER_TOKEN is empty. "
            "Set a shared bearer token, rotated every run (spec §2A.3.2).\n"
        )
        return 2
    if BIND_HOST not in ("127.0.0.1", "::1") and not ALLOW_IPS:
        sys.stderr.write(
            "[broker] FATAL: binding a non-loopback host without BROKER_ALLOW_IPS "
            "is refused — that would expose the key-holding broker to the LAN "
            "(spec §2A.3.1). Set BROKER_ALLOW_IPS to the guest's host-only IP.\n"
        )
        return 2

    httpd = ThreadingHTTPServer((BIND_HOST, BIND_PORT), BrokerHandler)
    _log("start", bind=f"{BIND_HOST}:{BIND_PORT}", allow_ips=list(ALLOW_IPS),
         models=sorted(ALLOWED_MODELS))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        _log("stop", **_meter.snapshot())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
