"""macos/guard.py — §2A.1 VM 守卫 + §2A.4 干净启动证明。

判定哲学（spec §2A.1，一字不让）：
  * 信号 A（安全锚点，必须命中）：system_profiler 的 Model Identifier
    **精确匹配**选定 VM 后端的已知机型串白名单。不用「包含 Virtual」这种
    模糊子串；真 Mac 机型（如 Mac17,3 / MacBookPro18,3）和任何未知串
    一律判非 VM → 拒绝。
  * 信号 B（附加必需因子，非"证明"）：VM 构建时预置的 secret 文件，
    root 拥有、0600、非空。守卫用 stat 校验（agent 用户读不了内容是
    设计使然——能校验存在性/属主/权限位就够了）。B 永不替代 A。
  * A 与 B 都指向"在 VM"才放行；否则 refuse。**无 --force 旁路。**
  * fail-closed 到底：整个守卫 try/except 包住，任何异常 / 子进程超时 /
    输出解析不出 → 拒绝。
  * 性能与 TOCTOU：机型串进程启动后读一次并缓存（安全锚点运行期不可变）；
    每个动作只再验一次便宜的信号 B（stat）。毫秒级 check→act 窗口已接受。

§2A.4 干净启动证明（consume_clean_token）：黄金镜像里预置一个一次性
token 文件；run_agent 启动时"消费"它（改名留痕）。还原镜像 → token 回来；
VM 长期不还原 → token 已被消费 → 拒绝启动。诚实交代：这挡的是**忘了还原**
的事故，不是挡镜像内攻击者（guest 被注入时 guest 里的一切都可伪造，
§2A.0 软墙#1）。
"""

from __future__ import annotations

import os
import re
import stat as stat_mod
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

# ---- 配置（可用环境变量覆盖，但白名单默认值必须是确定的 VM 机型串） ----

# Apple Virtualization 框架（UTM 的 Apple 后端 / Parallels 的 macOS guest）
# 里 macOS guest 的机型串。若 §3 选定其他后端，把它的确切机型串加进
# MACOS_AGENT_VM_MODELS（逗号分隔）——精确匹配，别加通配。
_DEFAULT_VM_MODELS = ("VirtualMac2,1",)

_SECRET_PATH_ENV = "MACOS_AGENT_VM_SECRET_PATH"
_DEFAULT_SECRET_PATH = "/etc/macos_agent/vm_secret"

_CLEAN_TOKEN_ENV = "MACOS_AGENT_CLEAN_TOKEN"
_DEFAULT_CLEAN_TOKEN = "~/.macos_agent/clean_token"

# system_profiler 子进程硬超时（秒）；超时 = 拒绝（spec §2A.1）
_SUBPROC_TIMEOUT = 10

_MODEL_RE = re.compile(r"Model Identifier:\s*(\S+)")


def _default_runner(cmd: list[str], timeout: float) -> str:
    """跑子进程拿 stdout；测试里可整体替换。超时/非零退出直接抛。"""
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, check=True,
    )
    return proc.stdout


class VmGuard:
    """VM 守卫。refusal_reason() 返回 "" 表示放行，非空即拒绝理由。"""

    def __init__(
        self,
        model_whitelist: Optional[tuple[str, ...]] = None,
        secret_path: Optional[str] = None,
        runner: Callable[[list[str], float], str] = _default_runner,
    ) -> None:
        env_models = os.getenv("MACOS_AGENT_VM_MODELS", "")
        if model_whitelist is not None:
            self.model_whitelist = tuple(model_whitelist)
        elif env_models.strip():
            self.model_whitelist = tuple(
                m.strip() for m in env_models.split(",") if m.strip()
            )
        else:
            self.model_whitelist = _DEFAULT_VM_MODELS
        self.secret_path = secret_path or os.getenv(
            _SECRET_PATH_ENV, _DEFAULT_SECRET_PATH
        )
        self._runner = runner
        # 信号 A 缓存：(读过没, 机型串或 None)
        self._model_cached = False
        self._model_value: Optional[str] = None

    # ---------------- 信号 A：机型（缓存一次） ---------------- #
    def _read_model_identifier(self) -> Optional[str]:
        if self._model_cached:
            return self._model_value
        model: Optional[str] = None
        try:
            out = self._runner(
                ["system_profiler", "SPHardwareDataType"], _SUBPROC_TIMEOUT
            )
            m = _MODEL_RE.search(out or "")
            if m:
                model = m.group(1).strip()
        except Exception:
            model = None  # 超时/崩溃/解析失败 → None → 拒绝
        self._model_cached = True
        self._model_value = model
        return model

    def _signal_a_reason(self) -> str:
        model = self._read_model_identifier()
        if not model:
            return "signal A failed: cannot read Model Identifier (fail-closed)"
        if model not in self.model_whitelist:
            return (
                f"signal A failed: model {model!r} is not in the VM whitelist "
                f"{list(self.model_whitelist)} (real Mac or unknown backend)"
            )
        return ""

    # ---------------- 信号 B：预置 secret（每动作 stat 一次） ---------------- #
    def _signal_b_reason(self) -> str:
        try:
            st = os.stat(self.secret_path)
        except OSError:
            return f"signal B failed: secret {self.secret_path} missing"
        if st.st_uid != 0:
            return "signal B failed: secret is not root-owned"
        mode = stat_mod.S_IMODE(st.st_mode)
        if mode & 0o077:
            return f"signal B failed: secret permissions too open ({oct(mode)})"
        if st.st_size <= 0:
            return "signal B failed: secret is empty"
        return ""

    # ---------------- 判定 ---------------- #
    def refusal_reason(self) -> str:
        """"" = 在 VM 里，放行；非空 = 拒绝理由。整体 fail-closed。"""
        try:
            reason_a = self._signal_a_reason()
            if reason_a:
                return f"refused: cannot prove VM ({reason_a})"
            reason_b = self._signal_b_reason()
            if reason_b:
                return f"refused: cannot prove VM ({reason_b})"
            return ""
        except Exception as e:  # noqa: BLE001 - 守卫自身出任何异常也拒绝
            return f"refused: cannot prove VM (guard error: {e})"

    def is_vm(self) -> bool:
        return self.refusal_reason() == ""


# ---------------- 模块级单例（进程启动读一次机型缓存） ---------------- #

_guard_singleton: Optional[VmGuard] = None


def get_guard() -> VmGuard:
    global _guard_singleton
    if _guard_singleton is None:
        _guard_singleton = VmGuard()
    return _guard_singleton


def reset_guard() -> None:
    """仅供测试：丢弃单例（含信号 A 缓存）。"""
    global _guard_singleton
    _guard_singleton = None


# 动作守卫覆盖表（spec §2A.2）：wait/done 真 inert，其余全过守卫。
INERT_ACTIONS = frozenset({"wait", "done"})


def action_refusal(action_name: str, guard: Optional[VmGuard] = None) -> str:
    """给执行器用：该动作要不要拦。"" = 放行。"""
    try:
        if action_name in INERT_ACTIONS:
            return ""
        g = guard if guard is not None else get_guard()
        return g.refusal_reason()
    except Exception as e:  # noqa: BLE001
        return f"refused: cannot prove VM (guard error: {e})"


# ---------------- §2A.4 干净启动证明 ---------------- #

def clean_token_path() -> Path:
    return Path(os.getenv(_CLEAN_TOKEN_ENV, _DEFAULT_CLEAN_TOKEN)).expanduser()


def consume_clean_token(path: Optional[Path] = None) -> tuple[bool, str]:
    """消费一次性干净启动 token。

    (True, "") = 证明了"自上次还原以来第一次跑"；
    (False, reason) = 证明不了 → 调用方必须拒绝启动（硬门，无旁路）。
    消费方式 = 原子改名成 .used.<ts>，留审计痕迹。
    """
    try:
        p = path if path is not None else clean_token_path()
        if not p.is_file():
            return (
                False,
                f"clean-start proof failed: token {p} missing — "
                "restore the VM from the golden image before running "
                "(spec §2A.4 hard gate, no bypass)",
            )
        used = p.with_name(p.name + f".used.{int(time.time())}")
        p.rename(used)
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, f"clean-start proof failed: {e} (fail-closed)"
