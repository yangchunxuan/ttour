"""循环守卫纯函数（macOS 桌面版）。

与网页版唯一的行为差异（§7A-M3）：`_blocked_type_reason` 是 no-op。
网页版按「控制词」(from/to/date/search…) 拦"非 goal 内文本"，在 macOS
桌面上会误拦往 TextEdit/Calculator 等字段输入的合理派生文本（A2/A3/A4），
所以这里恒返回 ""。签名保留，agent.py 无需感知差异。
"""

import hashlib
import json
from typing import Any
from urllib.parse import urlparse, urljoin

_CLOSED_BROWSER_MARKERS = (
    "target page, context or browser has been closed",
    "browser has been closed",
    "context has been closed",
    "page has been closed",
    "has no page",
)


def _action_signature(action: dict, url: str = "", element_text: str = "") -> str:
    """Stable key for loop detection: name + args + PAGE CONTEXT."""
    try:
        name = action.get("name", "?")
        args = action.get("args", {}) or {}
        page = (url or "").split("#")[0][:150]
        return (
            name
            + "::" + json.dumps(args, sort_keys=True, ensure_ascii=False)
            + "::" + page
            + "::" + (element_text or "")[:40]
        )
    except Exception:
        return repr(action)


def _is_error_payload(extracted: Any) -> bool:
    """An extraction that only carries an 'error' key is a failure, not data."""
    return isinstance(extracted, dict) and "error" in extracted


def _page_fingerprint(dom_state) -> str:
    """页面内容身份：URL + 可见文本哈希。"""
    url = (getattr(dom_state, "url", "") or "").split("#")[0]
    text = getattr(dom_state, "page_text", "") or ""
    return url + "::" + hashlib.md5(text.encode("utf-8", "replace")).hexdigest()


def _looks_like_closed_browser(message: str) -> bool:
    text = str(message or "").lower()
    return any(marker in text for marker in _CLOSED_BROWSER_MARKERS)


def _blocked_go_to_reason(url: Any, start_url: str, dom_state) -> str:
    target = str(url or "").strip()
    if not target:
        return "blocked go_to: empty URL"

    resolved = urljoin(getattr(dom_state, "url", "") or start_url, target)
    parsed = urlparse(resolved)
    if parsed.scheme not in ("http", "https"):
        return f"blocked go_to: unsupported URL scheme in {target!r}"

    start_host = _host(start_url)
    target_host = _host(resolved)
    if not target_host:
        return f"blocked go_to: invalid URL {target!r}"
    if _same_site(target_host, start_host):
        return ""
    if _same_site(target_host, _host(getattr(dom_state, "url", "") or "")):
        return ""

    for element in getattr(dom_state, "elements", []):
        href = ""
        try:
            href = str((getattr(element, "attributes", {}) or {}).get("href") or "")
        except Exception:
            href = ""
        if not href:
            continue
        linked = urljoin(getattr(dom_state, "url", "") or start_url, href)
        if _normalize_url(linked) == _normalize_url(resolved):
            return ""

    return (
        "blocked go_to: URL is outside the start site and is not a visible "
        f"current-page link ({resolved})"
    )


def _host(url: str) -> str:
    host = (urlparse(str(url or "")).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _site_key(host: str) -> str:
    parts = [p for p in host.split(".") if p]
    if len(parts) <= 2:
        return host
    return ".".join(parts[-2:])


def _same_site(left_host: str, right_host: str) -> bool:
    if not left_host or not right_host:
        return False
    return left_host == right_host or _site_key(left_host) == _site_key(right_host)


def _normalize_url(url: str) -> str:
    parsed = urlparse(str(url or ""))
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path.rstrip("/") or "/"
    return f"{scheme}://{host}{port}{path}?{parsed.query}"


def _arg_success(args: Any) -> bool:
    if not isinstance(args, dict):
        return True
    value = args.get("success", True)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "y", "1", "success")
    return bool(value)


def _page_has_observable_content(dom_state) -> bool:
    text = (getattr(dom_state, "page_text", "") or "").strip()
    elements = getattr(dom_state, "elements", []) or []
    return len(text) >= 80 or len(elements) >= 5


def _goal_needs_data(goal: str) -> bool:
    text = str(goal or "").lower()
    markers = (
        "提取", "获取", "收集", "抓取", "查询", "找", "json", "extract",
        "scrape", "collect", "result", "字段", "标题", "url",
    )
    return any(marker in text for marker in markers)


def _done_result_too_thin(args: Any) -> bool:
    if not isinstance(args, dict):
        return True
    result = args.get("result")
    if result is None:
        return True
    if isinstance(result, dict):
        meaningful = [
            key for key, value in result.items()
            if str(key).lower() not in {"ok", "success", "status", "message"}
            and value not in (None, "", [], {})
        ]
        return len(meaningful) < 2
    if isinstance(result, list):
        return len(result) == 0
    return len(str(result).strip()) < 30


def _blocked_type_reason(args: Any, dom_state, goal: str) -> str:
    """§7A-M3：macOS 桌面版恒放行。

    网页版的「控制词」启发式（出发/到达/date/search…）针对的是订票/检索
    表单里凭空编值的风险；桌面任务里往文本框输入的内容几乎全是 goal 的
    合理派生（如 A2 的 "Hello DeepSeek"），硬拦会把任务卡死。agent.py
    对每个 type 无条件调本函数，所以必须保留签名、恒返回 ""（不拦）。
    """
    return ""
