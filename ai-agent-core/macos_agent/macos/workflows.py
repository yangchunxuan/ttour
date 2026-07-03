"""macos/workflows.py — 复合工作流工具（把易错的多步序列收成可靠的一等动作）。

与 macos/actions.py 的分工：actions.py 是**原语动作**（click/type/press_key…，直接
一对一映射到一次 AX/CGEvent）；这里是**复合工作流**——把「保存对话框定目录」「Finder
新建文件夹」「产物落地自我验证」这类多步、易假成功的流程封装起来，注入前先断言上下文/
焦点，条件不对就诚实 ok=False，绝不无条件成功（A2/A4 教训 + 对抗评审的假成功防线）。

actions.py 在末尾 import 本模块并把 HANDLERS 并进 _HANDLERS；ACTION_SPEC 与 _HANDLERS
的一致性由 INV-04 / preflight 守。避免与 actions 顶层循环 import：本模块只从已定义好
ActionResult 的 actions 里取它（actions 在文件末尾才 import 本模块）。
"""

from __future__ import annotations

import asyncio

from macos import ax
from macos.actions import ActionResult


# 复合工具注入文本前，轮询等焦点真正落在「输入框」类控件上的参数。
# 换掉旧的固定 sleep：真机上 sheet 弹出 / 改名态就绪有可变延迟（冷启动/VM 负载下常
# 超过固定值），且焦点没落位时盲打会把文本灌进文档正文却仍"看似成功"（正是 A2/A4
# 要根除的假成功）。轮询到位才注入，超时就诚实 ok=False。
FOCUS_WAIT_TIMEOUT = 2.5
FOCUS_POLL_INTERVAL = 0.1
_FINDER_BUNDLE = "com.apple.finder"


def _inject_text_sync(text: str) -> tuple[bool, str]:
    """把 text 打到**当前已聚焦**的输入框（ascii 走 CGEvent，非 ascii 走剪贴板+Cmd+V）。
    复合工具用：调用前控件已因组合键自动聚焦，无需再解析编号。"""
    import time as _t
    if not text:
        return True, "empty"
    if text.isascii():
        if not ax.type_unicode(text):
            return False, "text injection failed"
    else:
        if not ax.set_clipboard(text):
            return False, "cannot write clipboard"
        if not ax.post_keycode(ax.KEYCODES["v"], cmd=True):
            return False, "Cmd+V failed"
    _t.sleep(0.1)
    return True, "ok"


def _wait_for_focused_role(roles: tuple[str, ...]) -> bool:
    """轮询直到当前聚焦元素的 AXRole 命中 roles 之一；超时返回 False。"""
    import time as _t
    deadline = _t.monotonic() + FOCUS_WAIT_TIMEOUT
    while _t.monotonic() < deadline:
        if ax.focused_element_role() in roles:
            return True
        _t.sleep(FOCUS_POLL_INTERVAL)
    return False


async def _do_go_to_folder(session, dom_state, args, planner) -> ActionResult:
    """工作流：保存/打开对话框或 Finder 里，用「前往文件夹」直接跳到 path。
    内部 cmd+shift+g → 确认前往框已聚焦 → 输 path → 回车。把易错的多步序列收成一个
    可靠动作（A2 教训）。焦点没落到输入框（如根本不在对话框里）就诚实失败，不假成功。"""
    path = str(args.get("path", "")).strip()
    if not path:
        return ActionResult(ok=False, message="go_to_folder failed: empty path")

    def _sync() -> tuple[bool, str]:
        if not ax.post_keycode(ax.KEYCODES["g"], cmd=True, shift=True):
            return False, "go_to_folder failed: Cmd+Shift+G post failed"
        # 「前往文件夹」小框是文本框/组合框；排除文档正文（AXTextArea）避免把路径灌进正文
        if not _wait_for_focused_role(("AXTextField", "AXComboBox")):
            return False, ("go_to_folder failed: 「前往文件夹」输入框未出现/未聚焦"
                           "——需在保存/打开对话框或 Finder 里用（避免把路径打进别处）")
        ok, msg = _inject_text_sync(path)
        if not ok:
            return False, f"go_to_folder failed: {msg}"
        ax.post_keycode(ax.KEYCODES["return"])
        return True, f"went to folder {path!r}"

    ok, msg = await asyncio.to_thread(_sync)
    return ActionResult(ok=ok, message=msg)


async def _do_new_folder(session, dom_state, args, planner) -> ActionResult:
    """工作流：在 Finder 当前位置新建名为 name 的文件夹。
    先断言前台确是 Finder（否则 cmd+shift+n 在别的 App 会走偏、把名字灌进文档还假成功）→
    cmd+shift+n → 确认改名输入框已聚焦 → 输名字 → 回车（A4 教训）。"""
    name = str(args.get("name", "")).strip()
    if not name:
        return ActionResult(ok=False, message="new_folder failed: empty name")

    def _sync() -> tuple[bool, str]:
        front = ax.frontmost_app()
        if front is None or front[2] != _FINDER_BUNDLE:
            return False, ("new_folder failed: 前台不是 Finder（先 launch_app('Finder')）；"
                           f"当前前台={front[1] if front else 'none'}")
        if not ax.post_keycode(ax.KEYCODES["n"], cmd=True, shift=True):
            return False, "new_folder failed: Cmd+Shift+N post failed"
        # 新建文件夹应进入 inline 改名态（一个聚焦的文本框）；Finder 里没有文档正文，
        # 所以 AXTextField 就绪 = 改名框到位。等不到 = 可能没建成，诚实失败。
        if not _wait_for_focused_role(("AXTextField",)):
            return False, "new_folder failed: 新文件夹改名输入框未就绪（可能未成功新建）"
        ok, msg = _inject_text_sync(name)
        if not ok:
            return False, f"new_folder failed: {msg}"
        ax.post_keycode(ax.KEYCODES["return"])
        return True, f"new folder named {name!r}"

    ok, msg = await asyncio.to_thread(_sync)
    return ActionResult(ok=ok, message=msg)


async def _do_verify_path(session, dom_state, args, planner) -> ActionResult:
    """接地气的自我验证：直接查 VM 真实文件系统，确认某路径的产物真的存在
    （治「假成功」的根——别靠 GUI 看着像成功就 done，A2 教训）。

    可选 contains=子串：确认某文件里真含这段文字。**只把布尔结论回传，绝不把文件
    正文放进 message/extracted**——正文经 broker→DeepSeek 就 egress 了（§2A.2）。"""
    raw = str(args.get("path", "")).strip()
    if not raw:
        return ActionResult(ok=False, message="verify_path failed: empty path")
    contains = args.get("contains")
    contains = None if contains in (None, "") else str(contains)

    def _sync() -> tuple[bool, str, dict]:
        import os
        p = os.path.expanduser(raw)
        exists = os.path.exists(p)
        is_file = os.path.isfile(p)
        is_dir = os.path.isdir(p)
        result = {"path": p, "exists": exists, "is_file": is_file, "is_dir": is_dir}
        ok = exists
        if contains is not None:
            hit = False
            if is_file:
                try:
                    with open(p, "r", encoding="utf-8", errors="replace") as fh:
                        hit = contains in fh.read(1_000_000)  # 只读进内存做判断
                except Exception as e:  # noqa: BLE001
                    result["read_error"] = str(e)
            result["contains_ok"] = hit   # 只留布尔，绝不回传正文
            ok = ok and hit
        msg = f"verify_path: {p} exists={exists} file={is_file} dir={is_dir}"
        if contains is not None:
            msg += f" contains({contains!r})={result['contains_ok']}"
        return ok, msg, result

    ok, msg, result = await asyncio.to_thread(_sync)
    return ActionResult(ok=ok, message=msg, extracted=result)


HANDLERS = {
    "go_to_folder": _do_go_to_folder,
    "new_folder": _do_new_folder,
    "verify_path": _do_verify_path,
}
