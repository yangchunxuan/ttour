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
    if not isinstance(args, dict):
        return ""
    text = str(args.get("text") or "").strip()
    if not text:
        return ""
    goal_text = str(goal or "")
    if text in goal_text:
        return ""
    try:
        element = dom_state.get(int(args.get("index")))
    except (TypeError, ValueError):
        element = None
    if element is None:
        return ""

    attrs = getattr(element, "attributes", {}) or {}
    haystack = " ".join(
        str(part or "") for part in (
            getattr(element, "text", ""),
            attrs.get("placeholder"),
            attrs.get("aria-label"),
            attrs.get("name"),
            attrs.get("id"),
            attrs.get("type"),
        )
    ).lower()
    controlled_fields = (
        "出发", "到达", "日期", "车次", "车站", "搜索", "查询",
        "from", "to", "date", "search", "keyword", "station", "train",
    )
    if any(marker in haystack for marker in controlled_fields):
        return (
            f"blocked type: value {text!r} was not provided by the user for "
            f"field [{args.get('index')}]"
        )
    return ""
