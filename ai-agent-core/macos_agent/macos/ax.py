"""macos/ax.py — 所有 pyobjc / Accessibility / CGEvent 调用的唯一出入口。

设计约束：
  * 懒加载：pyobjc 只在函数体内 import。宿主跑单测（可能没装 pyobjc）时，
    observe/actions 通过 monkeypatch 本模块函数即可完全脱离真 AX。
  * 返回值全部是纯 Python 类型（str/int/float/tuple/list/None）——
    AXUIElementRef / CFArray 是不透明对象，绝不 str() 它（spec §5），
    只把 ref 当句柄在本模块函数之间传递。
  * AX 调用的确切写法照抄 spec §5（返回值是 (err, value) 元组、
    AXUIElementSetMessagingTimeout 在 create 后立刻设、坐标无需 Y 翻转）。
"""

from __future__ import annotations

import time
from typing import Any, Optional

# kAXErrorSuccess
AX_OK = 0

# 打字时每次注入的最大字符数（CGEventKeyboardSetUnicodeString 的稳妥上限）
_TYPE_CHUNK = 16

# 常用美式键位 virtual keycode（press_key / 组合键用）
KEYCODES = {
    "return": 36, "enter": 36, "tab": 48, "space": 49, "delete": 51,
    "backspace": 51, "escape": 53, "esc": 53,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "pageup": 116, "pagedown": 121, "home": 115, "end": 119,
    "a": 0, "s": 1, "c": 8, "v": 9, "n": 45, "z": 6, "w": 13,
}


def ax_available() -> bool:
    try:
        import ApplicationServices  # noqa: F401
        import Quartz  # noqa: F401
        return True
    except Exception:
        return False


def is_process_trusted() -> bool:
    """Accessibility 授权检查（操作命脉，spec §3）。"""
    from ApplicationServices import AXIsProcessTrusted
    return bool(AXIsProcessTrusted())


# ------------------------------------------------------------------ #
# 前台 App / AX 元素树
# ------------------------------------------------------------------ #

def frontmost_app() -> Optional[tuple[int, str, str]]:
    """(pid, 应用名, bundle id)；没有前台 App 时返回 None。"""
    from Cocoa import NSWorkspace
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        return None
    return (
        int(app.processIdentifier()),
        str(app.localizedName() or ""),
        str(app.bundleIdentifier() or ""),
    )


def create_app_element(pid: int) -> Any:
    """建 App 级 AX 元素并立刻设 messaging 超时（spec §5：无 per-read 超时，
    不设的话一次卡死的读默认阻塞 ~6s）。"""
    from ApplicationServices import (
        AXUIElementCreateApplication,
        AXUIElementSetMessagingTimeout,
    )
    el = AXUIElementCreateApplication(pid)
    try:
        AXUIElementSetMessagingTimeout(el, 2.0)
    except Exception:
        pass  # 超时设不上不致命，遍历层还有 wall-clock 上限兜底
    return el


def copy_attr(el: Any, attr: str) -> Any:
    """AXUIElementCopyAttributeValue —— 返回值是 (err, value) 元组（spec §5）。
    失败返回 None。"""
    from ApplicationServices import AXUIElementCopyAttributeValue
    try:
        err, value = AXUIElementCopyAttributeValue(el, attr, None)
    except Exception:
        return None
    if err != AX_OK:
        return None
    return value


def copy_attr_str(el: Any, attr: str, max_len: int = 400) -> str:
    """读一个期望是文本/数值的属性，安全转成 str（不透明对象一律丢弃）。"""
    value = copy_attr(el, attr)
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)[:max_len]
    # NSString 桥接后就是 str；其余（AXUIElementRef/CFArray/AXValue）不硬转
    try:
        import objc  # noqa: F401
        from Foundation import NSString
        if isinstance(value, NSString):
            return str(value)[:max_len]
    except Exception:
        pass
    return ""


def children_of(el: Any) -> list:
    from ApplicationServices import kAXChildrenAttribute
    kids = copy_attr(el, kAXChildrenAttribute)
    if kids is None:
        return []
    try:
        return list(kids)
    except Exception:
        return []


def element_frame(el: Any) -> Optional[tuple[float, float, float, float]]:
    """(x, y, w, h)，全局坐标、左上原点、points——与 CGEvent 同系
    （spec §6：无需 Y 翻转、无需 Retina /2）。"""
    from ApplicationServices import (
        AXValueGetValue,
        kAXValueCGPointType,
        kAXValueCGSizeType,
    )
    pos_val = copy_attr(el, "AXPosition")
    size_val = copy_attr(el, "AXSize")
    if pos_val is None or size_val is None:
        return None
    try:
        ok1, pt = AXValueGetValue(pos_val, kAXValueCGPointType, None)
        ok2, sz = AXValueGetValue(size_val, kAXValueCGSizeType, None)
        if not (ok1 and ok2):
            return None
        return (float(pt.x), float(pt.y), float(sz.width), float(sz.height))
    except Exception:
        return None


def perform_action(el: Any, action: str = "AXPress") -> bool:
    """AXUIElementPerformAction 只回 err（spec §5）。"""
    from ApplicationServices import AXUIElementPerformAction
    try:
        return AXUIElementPerformAction(el, action) == AX_OK
    except Exception:
        return False


def set_attr(el: Any, attr: str, value: Any) -> bool:
    from ApplicationServices import AXUIElementSetAttributeValue
    try:
        return AXUIElementSetAttributeValue(el, attr, value) == AX_OK
    except Exception:
        return False


def element_actions(el: Any) -> list[str]:
    from ApplicationServices import AXUIElementCopyActionNames
    try:
        err, names = AXUIElementCopyActionNames(el, None)
        if err != AX_OK or names is None:
            return []
        return [str(n) for n in names]
    except Exception:
        return []


# ------------------------------------------------------------------ #
# CGEvent：鼠标 / 键盘 / 滚轮（全局坐标、左上原点、points）
# ------------------------------------------------------------------ #

def post_click(x: float, y: float) -> bool:
    from Quartz import (
        CGEventCreateMouseEvent, CGEventPost,
        kCGEventMouseMoved, kCGEventLeftMouseDown, kCGEventLeftMouseUp,
        kCGHIDEventTap, kCGMouseButtonLeft,
    )
    try:
        pt = (x, y)
        move = CGEventCreateMouseEvent(None, kCGEventMouseMoved, pt, kCGMouseButtonLeft)
        down = CGEventCreateMouseEvent(None, kCGEventLeftMouseDown, pt, kCGMouseButtonLeft)
        up = CGEventCreateMouseEvent(None, kCGEventLeftMouseUp, pt, kCGMouseButtonLeft)
        CGEventPost(kCGHIDEventTap, move)
        time.sleep(0.05)
        CGEventPost(kCGHIDEventTap, down)
        time.sleep(0.05)
        CGEventPost(kCGHIDEventTap, up)
        return True
    except Exception:
        return False


def type_unicode(text: str) -> bool:
    """CGEventKeyboardSetUnicodeString 注入 ASCII 文本（spec §6：
    中文/非 ASCII 走剪贴板+Cmd+V，别用这个）。"""
    from Quartz import (
        CGEventCreateKeyboardEvent, CGEventKeyboardSetUnicodeString,
        CGEventPost, kCGHIDEventTap,
    )
    try:
        for i in range(0, len(text), _TYPE_CHUNK):
            chunk = text[i:i + _TYPE_CHUNK]
            down = CGEventCreateKeyboardEvent(None, 0, True)
            CGEventKeyboardSetUnicodeString(down, len(chunk), chunk)
            CGEventPost(kCGHIDEventTap, down)
            up = CGEventCreateKeyboardEvent(None, 0, False)
            CGEventKeyboardSetUnicodeString(up, len(chunk), chunk)
            CGEventPost(kCGHIDEventTap, up)
            time.sleep(0.02)
        return True
    except Exception:
        return False


def post_keycode(keycode: int, cmd: bool = False, shift: bool = False,
                 option: bool = False, control: bool = False) -> bool:
    from Quartz import (
        CGEventCreateKeyboardEvent, CGEventPost, CGEventSetFlags,
        kCGHIDEventTap, kCGEventFlagMaskCommand, kCGEventFlagMaskShift,
        kCGEventFlagMaskAlternate, kCGEventFlagMaskControl,
    )
    try:
        flags = 0
        if cmd:
            flags |= kCGEventFlagMaskCommand
        if shift:
            flags |= kCGEventFlagMaskShift
        if option:
            flags |= kCGEventFlagMaskAlternate
        if control:
            flags |= kCGEventFlagMaskControl
        down = CGEventCreateKeyboardEvent(None, keycode, True)
        up = CGEventCreateKeyboardEvent(None, keycode, False)
        if flags:
            CGEventSetFlags(down, flags)
            CGEventSetFlags(up, flags)
        CGEventPost(kCGHIDEventTap, down)
        time.sleep(0.03)
        CGEventPost(kCGHIDEventTap, up)
        return True
    except Exception:
        return False


def post_scroll(amount_px: int) -> bool:
    """正值向上、负值向下（像素单位）。"""
    from Quartz import (
        CGEventCreateScrollWheelEvent, CGEventPost,
        kCGHIDEventTap, kCGScrollEventUnitPixel,
    )
    try:
        ev = CGEventCreateScrollWheelEvent(None, kCGScrollEventUnitPixel, 1, int(amount_px))
        CGEventPost(kCGHIDEventTap, ev)
        return True
    except Exception:
        return False


def set_clipboard(text: str) -> bool:
    """写 guest 内剪贴板（§4A 前提：跨宿主剪贴板共享已在 hypervisor 里关掉）。"""
    from Cocoa import NSPasteboard, NSPasteboardTypeString
    try:
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        return bool(pb.setString_forType_(text, NSPasteboardTypeString))
    except Exception:
        return False
