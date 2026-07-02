"""macos/actions.py — 执行器（spec §6）。

动作空间以 macos/prompts.py 的 ACTION_SPEC 为单一真相源：
click / type / select / scroll / press_key / launch_app / close_window /
wait / extract / done。

要点（spec §6 / §2A.2）：
  * 改状态动作 + extract 执行前过 VM 守卫；wait/done 真 inert。
  * click 优先 AXPress，不支持再算屏幕坐标 CGEvent 点
    （AX 与 CGEvent 同为全局、左上原点、points——无需任何变换）。
  * type：ASCII 用 CGEvent；中文/非 ASCII 用剪贴板+Cmd+V（§4A 已关跨宿主剪贴板）。
  * press_key 有 allowlist——§6A 诚实交代：它不是安全边界，只是 UX 引导。
  * 每动作 15s 超时，返回 ActionResult；stale 元素按 §5A 重定位，失败
    返回 ok=False（stale 是常态，不抛异常）。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from dataclasses import dataclass
from typing import Any, Optional

from . import ax
from .guard import action_refusal
from .observe import relocate

ACTION_TIMEOUT = 15.0  # 每动作硬超时（秒）
_LAUNCH_TIMEOUT = 10

# launch_app 白名单（§6A：不是安全边界，只是给模型的 UX 引导；
# VM 内任意执行视为可达，隔离靠 VM+§4A）。
DEFAULT_ALLOWED_APPS = ("TextEdit", "Calculator", "Finder", "Notes", "Preview")


def allowed_apps() -> tuple[str, ...]:
    extra = os.getenv("MACOS_AGENT_ALLOWED_APPS", "")
    apps = list(DEFAULT_ALLOWED_APPS)
    for name in extra.split(","):
        name = name.strip()
        if name and name not in apps:
            apps.append(name)
    return tuple(apps)


# press_key 白名单：单键 + 少量组合键。挡 cmd+space（Spotlight）、cmd+tab
# 等系统级切换（同样：UX 引导，非安全边界）。
ALLOWED_SINGLE_KEYS = frozenset({
    "enter", "return", "escape", "esc", "tab", "space", "delete", "backspace",
    "up", "down", "left", "right", "pageup", "pagedown", "home", "end",
})
ALLOWED_COMBOS = frozenset({
    "cmd+s", "cmd+shift+s", "cmd+a", "cmd+c", "cmd+v", "cmd+n", "cmd+z", "cmd+w",
})


@dataclass
class ActionResult:
    ok: bool
    message: str = ""
    extracted: Optional[dict] = None
    done: bool = False


# ------------------------------------------------------------------ #
# 入口：与 brain.agent 钉死的签名 execute(session, dom_state, action, planner)
# ------------------------------------------------------------------ #

async def execute(session, dom_state, action: dict, planner) -> ActionResult:
    name = str((action or {}).get("name", ""))
    args = (action or {}).get("args") or {}
    if not isinstance(args, dict):
        args = {}

    # 真 inert 的动作不过守卫（spec §2A.2 覆盖表）
    if name == "wait":
        seconds = args.get("seconds", 1)
        try:
            seconds = max(0.0, min(float(seconds), ACTION_TIMEOUT))
        except (TypeError, ValueError):
            seconds = 1.0
        await asyncio.sleep(seconds)
        return ActionResult(ok=True, message=f"waited {seconds}s")

    if name == "done":
        from brain.loop_guards import _arg_success
        result = args.get("result")
        if result is not None and not isinstance(result, (dict, list)):
            result = {"result": result}
        return ActionResult(
            ok=_arg_success(args),
            message=str(args.get("message", "")),
            extracted=result if isinstance(result, dict) else (
                {"items": result} if isinstance(result, list) else None
            ),
            done=True,
        )

    # 其余动作（含 extract）全过守卫——fail-closed，无旁路
    refusal = action_refusal(name, getattr(session, "guard", None))
    if refusal:
        return ActionResult(ok=False, message=refusal)

    handler = _HANDLERS.get(name)
    if handler is None:
        return ActionResult(ok=False, message=f"unknown action {name!r}")

    try:
        return await asyncio.wait_for(
            handler(session, dom_state, args, planner), timeout=ACTION_TIMEOUT
        )
    except asyncio.TimeoutError:
        return ActionResult(ok=False, message=f"action {name} timed out after {ACTION_TIMEOUT}s")
    except Exception as e:  # noqa: BLE001 - 执行器绝不让异常杀掉循环
        return ActionResult(ok=False, message=f"action {name} failed: {e}")


async def dismiss_popups(session) -> bool:
    """恢复路径的弹窗清理：按一次 Escape。真实按键前先过守卫（spec §7A-M7）。"""
    if action_refusal("press_key", getattr(session, "guard", None)):
        return False
    try:
        return await asyncio.to_thread(ax.post_keycode, ax.KEYCODES["escape"])
    except Exception:
        return False


# ------------------------------------------------------------------ #
# 元素解析（§5A：优先缓存 ref，stale 则按描述符重定位，再失败 ok=False）
# ------------------------------------------------------------------ #

def _resolve_element(dom_state, index) -> tuple[Optional[Any], Optional[Any], str]:
    """返回 (element_snapshot, live_ref, error)。error 非空即失败。"""
    el = dom_state.get(index)
    if el is None:
        return None, None, f"invalid index {index!r}: not in the current element list"
    ref = getattr(el, "ref", None)
    if ref is not None:
        # 便宜的活性探测：读一次 role，读不到就当 stale
        if ax.copy_attr_str(ref, "AXRole"):
            return el, ref, ""
    fresh = relocate(getattr(el, "descriptor", {}) or {})
    if fresh is not None:
        return el, fresh, ""
    return el, None, (
        f"stale element [{index}]: it no longer exists in the current window; "
        "re-observe and pick a fresh index"
    )


def _center_of(ref) -> Optional[tuple[float, float]]:
    frame = ax.element_frame(ref)
    if frame is None:
        return None
    x, y, w, h = frame
    return (x + w / 2.0, y + h / 2.0)


def _click_ref(ref) -> tuple[bool, str]:
    """AXPress 优先；不支持则 CGEvent 点中心（spec §6，坐标无需变换）。"""
    if "AXPress" in ax.element_actions(ref) and ax.perform_action(ref, "AXPress"):
        return True, "clicked via AXPress"
    center = _center_of(ref)
    if center is None:
        return False, "click failed: AXPress unsupported and element has no frame"
    if ax.post_click(center[0], center[1]):
        return True, f"clicked at ({center[0]:.0f},{center[1]:.0f}) via CGEvent"
    return False, "click failed: CGEvent post failed"


# ------------------------------------------------------------------ #
# 各动作 handler（签名统一：session, dom_state, args, planner）
# ------------------------------------------------------------------ #

async def _do_click(session, dom_state, args, planner) -> ActionResult:
    el, ref, err = _resolve_element(dom_state, args.get("index"))
    if err:
        return ActionResult(ok=False, message=err)
    ok, msg = await asyncio.to_thread(_click_ref, ref)
    return ActionResult(ok=ok, message=f"{msg} [{el.render()}]" if ok else msg)


async def _do_type(session, dom_state, args, planner) -> ActionResult:
    el, ref, err = _resolve_element(dom_state, args.get("index"))
    if err:
        return ActionResult(ok=False, message=err)
    text = str(args.get("text", ""))
    submit = bool(args.get("submit", False))

    def _type_sync() -> tuple[bool, str]:
        # 先把焦点放到目标控件：AXFocused，失败就点一下
        focused = ax.set_attr(ref, "AXFocused", True)
        if not focused:
            ok_click, _ = _click_ref(ref)
            if not ok_click:
                return False, "type failed: cannot focus the element"
        import time as _t
        _t.sleep(0.15)
        if text:
            if text.isascii():
                if not ax.type_unicode(text):
                    return False, "type failed: CGEvent text injection failed"
            else:
                # 中文/非 ASCII：剪贴板 + Cmd+V（spec §6；§4A 已关跨宿主剪贴板）
                if not ax.set_clipboard(text):
                    return False, "type failed: cannot write clipboard"
                if not ax.post_keycode(ax.KEYCODES["v"], cmd=True):
                    return False, "type failed: Cmd+V post failed"
            _t.sleep(0.1)
        if submit:
            ax.post_keycode(ax.KEYCODES["return"])
        return True, f"typed {len(text)} chars" + (" + Enter" if submit else "")

    ok, msg = await asyncio.to_thread(_type_sync)
    return ActionResult(ok=ok, message=msg)


async def _do_select(session, dom_state, args, planner) -> ActionResult:
    el, ref, err = _resolve_element(dom_state, args.get("index"))
    if err:
        return ActionResult(ok=False, message=err)
    value = str(args.get("value", ""))

    def _select_sync() -> tuple[bool, str]:
        import time as _t
        # 弹出按钮/下拉：展开后在子树里按可见文本找菜单项
        ok, _ = _click_ref(ref)
        if not ok:
            return False, "select failed: cannot open the control"
        _t.sleep(0.4)
        stack = list(ax.children_of(ref))
        # 弹出的菜单有时挂在 App 层——补扫前台 App 的 AXMenu
        front = ax.frontmost_app()
        if front is not None:
            app_el = ax.create_app_element(front[0])
            for w in ax.children_of(app_el):
                if ax.copy_attr_str(w, "AXRole") in ("AXMenu", "AXWindow"):
                    stack.append(w)
        seen = 0
        while stack and seen < 400:
            seen += 1
            node = stack.pop()
            role = ax.copy_attr_str(node, "AXRole")
            title = ax.copy_attr_str(node, "AXTitle")
            if role in ("AXMenuItem", "AXPopUpButton", "AXCell") and title == value:
                if ax.perform_action(node, "AXPress"):
                    return True, f"selected {value!r}"
            stack.extend(ax.children_of(node))
        # 收起残留菜单，别把 UI 留在打开状态
        ax.post_keycode(ax.KEYCODES["escape"])
        return False, f"select failed: option {value!r} not found"

    ok, msg = await asyncio.to_thread(_select_sync)
    return ActionResult(ok=ok, message=msg)


async def _do_scroll(session, dom_state, args, planner) -> ActionResult:
    direction = str(args.get("direction", "down")).lower()
    if direction not in ("up", "down"):
        return ActionResult(ok=False, message=f"scroll failed: bad direction {direction!r}")
    try:
        amount = int(args.get("amount", 600))
    except (TypeError, ValueError):
        amount = 600
    amount = max(50, min(abs(amount), 4000))
    signed = amount if direction == "up" else -amount
    ok = await asyncio.to_thread(ax.post_scroll, signed)
    return ActionResult(ok=ok, message=f"scrolled {direction} {amount}px" if ok
                        else "scroll failed: CGEvent post failed")


def _parse_key(key: str) -> Optional[dict]:
    """'cmd+shift+s' -> {"keycode":1,"cmd":True,"shift":True}；不在白名单返回 None。"""
    norm = "+".join(p.strip().lower() for p in str(key).split("+") if p.strip())
    norm = norm.replace("command", "cmd").replace("option", "opt")
    if "+" not in norm:
        if norm not in ALLOWED_SINGLE_KEYS:
            return None
        code = ax.KEYCODES.get(norm)
        return {"keycode": code} if code is not None else None
    if norm not in ALLOWED_COMBOS:
        return None
    parts = norm.split("+")
    base = parts[-1]
    code = ax.KEYCODES.get(base)
    if code is None:
        return None
    return {
        "keycode": code,
        "cmd": "cmd" in parts[:-1],
        "shift": "shift" in parts[:-1],
        "option": "opt" in parts[:-1],
        "control": "ctrl" in parts[:-1] or "control" in parts[:-1],
    }


async def _do_press_key(session, dom_state, args, planner) -> ActionResult:
    key = str(args.get("key", ""))
    spec = _parse_key(key)
    if spec is None:
        return ActionResult(
            ok=False,
            message=(
                f"press_key blocked: {key!r} is not in the allowlist "
                f"(singles: {sorted(ALLOWED_SINGLE_KEYS)}; combos: {sorted(ALLOWED_COMBOS)})"
            ),
        )
    ok = await asyncio.to_thread(ax.post_keycode, spec["keycode"],
                                 spec.get("cmd", False), spec.get("shift", False),
                                 spec.get("option", False), spec.get("control", False))
    return ActionResult(ok=ok, message=f"pressed {key}" if ok else f"press_key {key} failed")


async def _do_launch_app(session, dom_state, args, planner) -> ActionResult:
    app = str(args.get("app", "")).strip()
    apps = allowed_apps()
    if app not in apps:
        return ActionResult(
            ok=False,
            message=f"launch_app blocked: {app!r} not in the app whitelist {list(apps)}",
        )

    def _launch_sync() -> tuple[bool, str]:
        try:
            proc = subprocess.run(
                ["open", "-a", app], capture_output=True, text=True,
                timeout=_LAUNCH_TIMEOUT,
            )
            if proc.returncode != 0:
                return False, f"launch_app failed: {proc.stderr.strip() or proc.returncode}"
            import time as _t
            _t.sleep(1.0)  # 给 App 一点前台化时间
            return True, f"launched {app}"
        except Exception as e:  # noqa: BLE001
            return False, f"launch_app failed: {e}"

    ok, msg = await asyncio.to_thread(_launch_sync)
    return ActionResult(ok=ok, message=msg)


async def _do_close_window(session, dom_state, args, planner) -> ActionResult:
    def _close_sync() -> tuple[bool, str]:
        front = ax.frontmost_app()
        if front is None:
            return False, "close_window failed: no frontmost app"
        app_el = ax.create_app_element(front[0])
        windows = ax.copy_attr(app_el, "AXWindows")
        try:
            windows = list(windows) if windows is not None else []
        except Exception:
            windows = []
        if not windows:
            return False, "close_window failed: app has no windows"
        target = windows[0]
        btn = ax.copy_attr(target, "AXCloseButton")
        if btn is not None and ax.perform_action(btn, "AXPress"):
            return True, "window closed"
        # 兜底 Cmd+W（在组合键白名单里）
        if ax.post_keycode(ax.KEYCODES["w"], cmd=True):
            return True, "window closed via Cmd+W"
        return False, "close_window failed"

    ok, msg = await asyncio.to_thread(_close_sync)
    return ActionResult(ok=ok, message=msg)


async def _do_extract(session, dom_state, args, planner) -> ActionResult:
    """extract 不改桌面状态，但经 broker 把屏幕文本 egress 到 DeepSeek，
    所以照 §2A.2 过守卫（在 execute() 入口已统一过）。"""
    schema = str(args.get("schema", "")).strip()
    if not schema:
        return ActionResult(ok=False, message="extract failed: empty schema")
    element_lines = "\n".join(el.render() for el in dom_state.elements[:120])
    source = (
        f"URL: {dom_state.url}\nTITLE: {dom_state.title}\n\n"
        f"VISIBLE WINDOW TEXT:\n{dom_state.page_text}\n\n"
        f"VISIBLE CONTROLS:\n{element_lines}"
    )
    result = await planner.extract(source, schema)
    if isinstance(result, dict) and "error" in result:
        return ActionResult(ok=False, message=f"extract failed: {result['error']}")
    if not isinstance(result, dict):
        result = {"result": result}
    return ActionResult(ok=True, message="extracted", extracted=result)


_HANDLERS = {
    "click": _do_click,
    "type": _do_type,
    "select": _do_select,
    "scroll": _do_scroll,
    "press_key": _do_press_key,
    "launch_app": _do_launch_app,
    "close_window": _do_close_window,
    "extract": _do_extract,
}
