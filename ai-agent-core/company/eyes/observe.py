"""macos/observe.py — AX 控件树 -> MacDomState（spec §5）。

接口对齐网页 DomState，循环实际消费的字段全都有：
  MacDomState: .url / .title / .page_text / .get(index) / .elements / .to_prompt()
  MacElement:  .index / .text / .attributes / .render() + role/title/value/window_title

遍历约束（spec §5）：只枚举前台 App 的可见窗口；总元素 ≤300、单 App 3s
wall-clock 上限、限深度；每次 observe 重编号，编号只在本次有效。

§5A：不缓存裸 AXUIElementRef 当主键——每个元素同时存复合描述符
(role, title, AXIdentifier, bundle id, 窗口标题, 同级序号)，执行器按它
在当前树重定位；stale 是常态，不抛异常。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from . import ax

# 遍历上限（spec §5）
MAX_ELEMENTS = 300
MAX_SECONDS_PER_APP = 3.0
MAX_DEPTH = 25
# 喂给模型的可见文本上限（llm 层还有 28K 兜底截断）
MAX_PAGE_TEXT = 12000

# 可交互角色 -> 进编号清单
INTERACTIVE_ROLES = frozenset({
    "AXButton", "AXTextField", "AXTextArea", "AXSearchField",
    "AXCheckBox", "AXRadioButton", "AXPopUpButton", "AXComboBox",
    "AXMenuButton", "AXMenuItem", "AXMenuBarItem", "AXLink",
    "AXIncrementor", "AXSlider", "AXTabGroup", "AXDisclosureTriangle",
    "AXCell", "AXRow", "AXOutlineRow",
})

# 纯文本角色 -> 进 page_text
TEXT_ROLES = frozenset({"AXStaticText", "AXTextField", "AXTextArea", "AXHeading"})


@dataclass
class MacElement:
    index: int
    role: str = ""
    title: str = ""
    value: str = ""
    window_title: str = ""
    app_name: str = ""
    bundle_id: str = ""
    text: str = ""                      # 循环用 el.text 建签名（spec §5）
    attributes: dict = field(default_factory=dict)
    descriptor: dict = field(default_factory=dict)  # §5A 复合描述符
    ref: Any = None                     # 裸 AXUIElementRef——只当加速缓存，永不当主键

    def render(self) -> str:
        """_auto_extract_if_possible 每元素调（spec §5：用 role/title 拼，
        别照抄网页版 self.tag 的写法）。"""
        role = self.role.replace("AX", "", 1) if self.role.startswith("AX") else self.role
        label = self.text or self.title or self.value
        return f"[{self.index}] <{role}> {label}".strip()


@dataclass
class MacDomState:
    url: str = "macos://desktop"
    title: str = ""
    page_text: str = ""
    elements: list = field(default_factory=list)
    app_name: str = ""
    bundle_id: str = ""
    frame_count: int = 1  # health.py 的 signals 会读它（duck-typing 兼容）

    def get(self, index) -> Optional[MacElement]:
        try:
            idx = int(index)
        except (TypeError, ValueError):
            return None
        for el in self.elements:
            if el.index == idx:
                return el
        return None

    def to_prompt(self) -> str:
        lines = [
            f"前台应用: {self.app_name}  窗口: {self.title}",
            "",
            "## 可交互控件（用方括号里的编号操作）",
        ]
        if self.elements:
            lines.extend(el.render() for el in self.elements)
        else:
            lines.append("（本窗口没有识别到可交互控件——考虑 launch_app / wait / press_key Escape）")
        lines.append("")
        lines.append("## 窗口可见文本")
        text = self.page_text or "（无可见文本）"
        if len(text) > MAX_PAGE_TEXT:
            head = text[: int(MAX_PAGE_TEXT * 0.7)]
            tail = text[-int(MAX_PAGE_TEXT * 0.25):]
            text = f"{head}\n…[中间内容省略]…\n{tail}"
        lines.append(text)
        return "\n".join(lines)


# ------------------------------------------------------------------ #
# 遍历
# ------------------------------------------------------------------ #

def _element_snapshot(el: Any, window_title: str, app_name: str, bundle_id: str,
                      sibling_ordinal: int, index: int) -> MacElement:
    role = ax.copy_attr_str(el, "AXRole")
    title = ax.copy_attr_str(el, "AXTitle")
    value = ax.copy_attr_str(el, "AXValue")
    desc = ax.copy_attr_str(el, "AXDescription")
    identifier = ax.copy_attr_str(el, "AXIdentifier")
    placeholder = ax.copy_attr_str(el, "AXPlaceholderValue")
    help_text = ax.copy_attr_str(el, "AXHelp")

    text = title or value or desc or placeholder or help_text or ""
    attributes = {}
    for key, val in (
        ("identifier", identifier), ("description", desc),
        ("placeholder", placeholder), ("help", help_text),
    ):
        if val:
            attributes[key] = val

    return MacElement(
        index=index,
        role=role,
        title=title,
        value=value[:200],
        window_title=window_title,
        app_name=app_name,
        bundle_id=bundle_id,
        text=text[:200],
        attributes=attributes,
        descriptor={
            "role": role,
            "title": title,
            "identifier": identifier,
            "bundle_id": bundle_id,
            "window_title": window_title,
            "sibling_ordinal": sibling_ordinal,
        },
        ref=el,
    )


def _walk_window(window: Any, window_title: str, app_name: str, bundle_id: str,
                 elements: list, texts: list, deadline: float) -> None:
    """迭代式 DFS（显式栈），带元素数/时间/深度三重上限。"""
    stack: list[tuple[Any, int, int]] = [(window, 0, 0)]  # (el, depth, sibling_ordinal)
    while stack:
        if time.monotonic() > deadline or len(elements) >= MAX_ELEMENTS:
            return
        el, depth, ordinal = stack.pop()
        role = ax.copy_attr_str(el, "AXRole")

        if role in TEXT_ROLES:
            value = ax.copy_attr_str(el, "AXValue", max_len=2000)
            title = ax.copy_attr_str(el, "AXTitle")
            snippet = value or title
            if snippet:
                texts.append(snippet)

        if role in INTERACTIVE_ROLES and len(elements) < MAX_ELEMENTS:
            snap = _element_snapshot(
                el, window_title, app_name, bundle_id, ordinal, len(elements)
            )
            # 完全空白的控件（无 role 文本信息）对模型无意义，跳过
            if snap.role and (snap.text or snap.role not in ("AXCell", "AXRow")):
                elements.append(snap)

        if depth >= MAX_DEPTH:
            continue
        kids = ax.children_of(el)
        # 倒序压栈保持文档序弹出
        for i in range(len(kids) - 1, -1, -1):
            stack.append((kids[i], depth + 1, i))


def _focus_fingerprint(app_el: Any) -> str:
    """焦点信息进 url——多带点会随焦点变的东西，让 _page_fingerprint 有信号（spec §5）。"""
    try:
        focused = ax.copy_attr(app_el, "AXFocusedUIElement")
        if focused is None:
            return "none"
        role = ax.copy_attr_str(focused, "AXRole")
        title = ax.copy_attr_str(focused, "AXTitle")
        return f"{role}:{title}"[:80] or "unknown"
    except Exception:
        return "unknown"


async def observe(session) -> MacDomState:
    """观察前台应用 -> MacDomState。只读，任何环境可跑（spec §2A.1 末行）。

    注意：宿主上开发跑 observe 会读到宿主敏感 UI——别记日志。
    """
    front = ax.frontmost_app()
    if front is None:
        return MacDomState(
            url="macos://no-frontmost-app",
            title="",
            page_text="",
            elements=[],
        )
    pid, app_name, bundle_id = front
    app_el = ax.create_app_element(pid)

    windows = ax.copy_attr(app_el, "AXWindows")
    try:
        windows = list(windows) if windows is not None else []
    except Exception:
        windows = []
    # 前台窗口优先（AXMain），其余可见窗口靠后
    if len(windows) > 1:
        def _is_main(w):
            return ax.copy_attr_str(w, "AXMain") in ("1", "True", "true")
        windows.sort(key=lambda w: 0 if _is_main(w) else 1)

    elements: list = []
    texts: list = []
    deadline = time.monotonic() + MAX_SECONDS_PER_APP
    first_window_title = ""
    for w in windows:
        window_title = ax.copy_attr_str(w, "AXTitle")
        if not first_window_title:
            first_window_title = window_title
        _walk_window(w, window_title, app_name, bundle_id, elements, texts, deadline)
        if time.monotonic() > deadline or len(elements) >= MAX_ELEMENTS:
            break

    # 菜单栏不遍历（元素爆炸且 launch_app/press_key 已覆盖主要路径）

    focus = _focus_fingerprint(app_el)
    title = first_window_title or app_name

    # 可见文本去重保序
    seen = set()
    unique_texts = []
    for t in texts:
        if t not in seen:
            seen.add(t)
            unique_texts.append(t)

    return MacDomState(
        url=f"macos://{app_name}/{title}#focus={focus}&els={len(elements)}",
        title=title,
        page_text="\n".join(unique_texts)[: MAX_PAGE_TEXT * 2],
        elements=elements,
        app_name=app_name,
        bundle_id=bundle_id,
    )


# ------------------------------------------------------------------ #
# §5A 元素再定位（执行器用）
# ------------------------------------------------------------------ #

def relocate(descriptor: dict) -> Optional[Any]:
    """按复合描述符在**当前**前台树里重找元素，返回新鲜 AXUIElementRef 或 None。

    匹配优先级：identifier > (role+title) > (role+同级序号，同窗口)。
    找不到就 None——调用方返回 ok=False 让循环重观察，不抛异常。
    """
    try:
        front = ax.frontmost_app()
        if front is None:
            return None
        pid, app_name, bundle_id = front
        if descriptor.get("bundle_id") and bundle_id and \
                descriptor["bundle_id"] != bundle_id:
            return None  # 前台已换 App，旧描述符必然失效

        app_el = ax.create_app_element(pid)
        windows = ax.copy_attr(app_el, "AXWindows")
        try:
            windows = list(windows) if windows is not None else []
        except Exception:
            windows = []

        want_role = descriptor.get("role", "")
        want_title = descriptor.get("title", "")
        want_ident = descriptor.get("identifier", "")
        want_ordinal = descriptor.get("sibling_ordinal", -1)

        best = None
        best_score = 0
        deadline = time.monotonic() + MAX_SECONDS_PER_APP
        for w in windows:
            stack: list[tuple[Any, int, int]] = [(w, 0, 0)]
            while stack:
                if time.monotonic() > deadline:
                    return best
                el, depth, ordinal = stack.pop()
                role = ax.copy_attr_str(el, "AXRole")
                if role == want_role:
                    score = 1
                    if want_ident and ax.copy_attr_str(el, "AXIdentifier") == want_ident:
                        score = 4
                    elif want_title and ax.copy_attr_str(el, "AXTitle") == want_title:
                        score = 3
                    elif ordinal == want_ordinal:
                        score = 2
                    if score > best_score:
                        best, best_score = el, score
                        if score >= 4:
                            return best
                if depth >= MAX_DEPTH:
                    continue
                kids = ax.children_of(el)
                for i in range(len(kids) - 1, -1, -1):
                    stack.append((kids[i], depth + 1, i))
        # 只有强匹配（identifier/title/序号命中）才算重定位成功；
        # 光 role 相同（score=1）太弱，宁可让上层重观察。
        return best if best_score >= 2 else None
    except Exception:
        return None
