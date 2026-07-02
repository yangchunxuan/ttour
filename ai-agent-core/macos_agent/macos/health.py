"""macos/health.py — 桌面版页面健康桩（spec §7A-M1）。

网页版 assess_page_health 的坏页标记（login/captcha/cloudflare…）在桌面
观察里全是噪声（如备忘录正文里一句"请登录"会把整个观察判成 login_required
→ bad → 提前中止）。spec §7A-M1 明确允许"恒返回 PageHealth('ok',...) 的桩"，
这里就用它——但保留最基本的可用性信号：完全没有前台 App 时给 loading，
让循环触发 hint 而不是瞎点。

复用 brain.health.PageHealth dataclass（循环读 .ok/.bad/.reason/.to_hint()/.signals）。
"""

from __future__ import annotations

from brain.health import PageHealth


def assess_mac_health(dom_state) -> PageHealth:
    url = str(getattr(dom_state, "url", "") or "")
    text = str(getattr(dom_state, "page_text", "") or "")
    elements = getattr(dom_state, "elements", []) or []
    signals = {
        "url": url,
        "text_length": len(text.strip()),
        "element_count": len(elements),
    }
    if url.startswith("macos://no-frontmost-app"):
        # 非 bad：循环只 emit hint 继续跑（模型该 launch_app / wait）
        return PageHealth("loading", "no frontmost application detected", signals)
    return PageHealth("ok", "desktop observation available", signals)
