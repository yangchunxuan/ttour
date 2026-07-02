"""Page health classification for the browser agent.

This module is deliberately deterministic. The model may still reason about a
page, but basic source usability should not depend on vibes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


BAD_STATUS = {"blocked", "captcha", "login_required", "dead"}


@dataclass
class PageHealth:
    status: str
    reason: str
    signals: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def bad(self) -> bool:
        return self.status in BAD_STATUS

    def to_hint(self) -> str:
        return f"PAGE_HEALTH {self.status}: {self.reason}"


def assess_page_health(dom_state) -> PageHealth:
    """Classify whether the current observation is usable.

    The classifier uses only observable page signals: URL, title, visible text,
    and detected controls. It intentionally avoids network-level assumptions so
    it can run after every `observe()`.
    """
    url = str(getattr(dom_state, "url", "") or "")
    title = str(getattr(dom_state, "title", "") or "")
    text = str(getattr(dom_state, "page_text", "") or "")
    elements = getattr(dom_state, "elements", []) or []

    text_l = text.lower()
    title_l = title.lower()
    joined = f"{title_l}\n{text_l}"

    signals = {
        "url": url,
        "title": title,
        "text_length": len(text.strip()),
        "element_count": len(elements),
        "frame_count": getattr(dom_state, "frame_count", 1),
    }

    if not url or url == "about:blank":
        return PageHealth("dead", "page has no usable URL", signals)

    # 基于文字标记的坏页判定只在页面内容很短时才可信：真正的拦截页/验证码页/
    # 错误页几乎没有正文，而正常页面的新闻标题/正文里完全可能出现
    # "cloudflare"/"captcha"/"not found" 这类词（实测 HN 首页因一条提到
    # Cloudflare 的新闻被误判为 blocked，被劫持进降级抽取路径）。
    marker_trustworthy = len(text.strip()) < 600

    if marker_trustworthy and any(
        code in title_l for code in ("404", "500", "502", "503", "504")
    ):
        return PageHealth("dead", f"title looks like an error page: {title}", signals)

    dead_markers = (
        "not found",
        "page not found",
        "server error",
        "service unavailable",
        "bad gateway",
        "gateway timeout",
        "dns_probe",
        "this site can't be reached",
        "无法访问此网站",
        "找不到网页",
        "网页无法打开",
        "服务器错误",
    )
    if marker_trustworthy and any(marker in joined for marker in dead_markers):
        return PageHealth("dead", "page text indicates an error or unreachable site", signals)

    captcha_markers = (
        "captcha",
        "recaptcha",
        "hcaptcha",
        "verify you are human",
        "human verification",
        "security check",
        "验证码",
        "人机验证",
        "请完成验证",
    )
    if marker_trustworthy and any(marker in joined for marker in captcha_markers):
        return PageHealth("captcha", "page appears to require human verification", signals)

    blocked_markers = (
        "access denied",
        "forbidden",
        "request blocked",
        "temporarily blocked",
        "unusual traffic",
        "cloudflare",
        "被拒绝访问",
        "访问受限",
        "请求被拦截",
        "异常流量",
    )
    if marker_trustworthy and any(marker in joined for marker in blocked_markers):
        return PageHealth("blocked", "page appears blocked or bot-protected", signals)

    login_markers = (
        "sign in",
        "log in",
        "login required",
        "please login",
        "please sign in",
        "登录",
        "请登录",
        "需要登录",
    )
    if any(marker in joined for marker in login_markers) and len(text.strip()) < 400:
        return PageHealth("login_required", "page appears to require login", signals)

    if len(text.strip()) < 20 and len(elements) == 0:
        return PageHealth("loading", "page has almost no text or controls yet", signals)

    if len(text.strip()) >= 80:
        return PageHealth("ok", "page has readable visible text", signals)

    if len(elements) >= 5:
        return PageHealth("ok", "page has enough visible controls to operate", signals)

    if len(elements) == 0:
        return PageHealth("unusable", "page has little text and no detected controls", signals)

    return PageHealth("ok", "page has limited but usable content", signals)
