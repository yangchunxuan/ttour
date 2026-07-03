"""macos/preflight.py — 开跑前的**配置自检**（fail-fast，与安全硬门分开）。

harness engineering 的核心洞见：真正的瓶颈常是「环境/配置被悄悄写错」而不是模型。
这个自检在 agent 正式跑之前，把**可静态发现**的配置错误一次性报清楚 + 给出修复，
避免 agent 跑到一半因为「白名单里授权了一个没键码的键」「提示词教了个会被拦的键」
这类问题神秘卡死。

和 run_agent.py 的 `_preflight()` 分工：那个是安全硬门（干净启动 §2A.4 / VM 守卫
§2A.1）；这个是**配置健康**。三类「代码级一致性」检查是硬失败（破了 agent 必然
malfunction）；「应用是否安装 / AX 是否授权」是软警告（换个上下文可能就补上了）。

跑：python3 -m macos.preflight        # 或 run_agent.py --check
这些静态检查与 tools/lint_invariants.py 的 INV-04/13/15 同源，但这里作用于**运行时
真正 import 的模块**，是开跑前最后一道体检。
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Callable


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    fix: str = ""
    hard: bool = True  # hard=False 的失败只警告、不阻断启动


def check_whitelist_keycodes() -> Check:
    """白名单里每个键都必须解析得出键码（否则授权了却按不出——INV-15 的运行时版）。"""
    from macos import actions
    bad = [k for k in sorted(actions.ALLOWED_SINGLE_KEYS | actions.ALLOWED_COMBOS)
           if actions._parse_key(k) is None]
    return Check("whitelist↔keycode", not bad,
                 f"白名单里这些键解析不出键码：{bad}" if bad else "白名单每个键都有键码",
                 "在 macos/ax.py 的 KEYCODES 补对应键码（如 g=5），否则模型被授权却按不出")


def check_prompt_keys_allowed() -> Check:
    """系统提示词教模型按的组合键，必须都在 press_key 白名单里（INV-13 的运行时版）。"""
    from macos import actions, prompts
    text = prompts.get_system_prompt()
    bad = []
    for combo in re.findall(r"[A-Za-z]+(?:\s*\+\s*[A-Za-z0-9]+)+", text):
        norm = re.sub(r"\s+", "", combo.lower())
        if norm.split("+")[0] in {"cmd", "command", "ctrl", "control", "opt", "option", "shift"} \
           and actions._parse_key(norm) is None:
            bad.append(combo)
    uniq = sorted(set(bad))
    return Check("prompt↔whitelist", not uniq,
                 f"提示词教了这些不在白名单的键：{uniq}" if uniq else "提示词教的键都在白名单",
                 "把键补进 ALLOWED_COMBOS，或改提示词别教它")


def check_action_spec_parity() -> Check:
    """动作空间（提示词承诺）必须与 handlers（真能执行）一致（INV-04 的运行时版）。"""
    from macos import prompts, actions
    spec = set(prompts.ACTION_SPEC)
    handlers = set(actions._HANDLERS) | {"wait", "done"}
    diff = spec ^ handlers
    return Check("ACTION_SPEC↔handlers", not diff,
                 f"动作空间与 handlers 不一致：{sorted(diff)}" if diff else "动作空间一致",
                 "补齐缺的 handler 或从 ACTION_SPEC 删掉（INV-04）")


def check_allowed_apps_present() -> Check:
    """白名单应用在本机能否被 open 定位到（软警告：换台机器/VM 可能就有了）。"""
    from macos import actions
    missing = []
    for app in actions.allowed_apps():
        try:
            r = subprocess.run(["open", "-Ra", app], capture_output=True, timeout=5)
            if r.returncode != 0:
                missing.append(app)
        except Exception:  # noqa: BLE001
            missing.append(app)
    return Check("allowed apps present", not missing,
                 f"这些白名单应用在本机定位不到：{missing}" if missing else "白名单应用都在",
                 "确认这些应用已装，或用 MACOS_AGENT_ALLOWED_APPS 调整白名单", hard=False)


def check_ax_ready() -> Check:
    """辅助功能是否可用+已授权（软警告：off-VM/无 pyobjc 时本就不该硬失败）。"""
    from macos import ax
    if not ax.ax_available():
        return Check("AX available", False, "pyobjc/Quartz 不可用（off-VM 主机上正常）",
                     "在 guest 里装 pyobjc；本机/CI 上可忽略", hard=False)
    trusted = ax.is_process_trusted()
    return Check("AX trusted", trusted,
                 "AX 已授权" if trusted else "本进程没有辅助功能授权（读不到控件树、发不了键）",
                 "系统设置→隐私与安全性→辅助功能，给驱动本进程的 ssh/终端/python 打勾", hard=False)


CHECKS: list[Callable[[], Check]] = [
    check_whitelist_keycodes,
    check_prompt_keys_allowed,
    check_action_spec_parity,
    check_allowed_apps_present,
    check_ax_ready,
]


def run() -> list[Check]:
    out: list[Check] = []
    for fn in CHECKS:
        try:
            out.append(fn())
        except Exception as exc:  # noqa: BLE001 - 自检本身出错也当一条失败报出来
            out.append(Check(fn.__name__, False, f"自检执行出错：{exc}", "检查该项实现"))
    return out


def hard_failures(checks: list[Check]) -> list[Check]:
    return [c for c in checks if not c.ok and c.hard]


def format_report(checks: list[Check]) -> str:
    lines = []
    for c in checks:
        mark = "✅" if c.ok else ("❌" if c.hard else "⚠️")
        lines.append(f"{mark} {c.name}: {c.detail}")
        if not c.ok and c.fix:
            lines.append(f"    修复：{c.fix}")
    if hard_failures(checks):
        lines.append("preflight: 有硬失败（配置级 bug，agent 必然 malfunction）")
    elif all(c.ok for c in checks):
        lines.append("preflight: ok")
    else:
        lines.append("preflight: ok（仅软警告）")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    checks = run()
    print(format_report(checks))
    return 1 if hard_failures(checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
