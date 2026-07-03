"""tests/test_preflight.py — 配置自检（macos/preflight）单测。

静态一致性检查（whitelist↔keycode / prompt↔whitelist / ACTION_SPEC↔handlers）
作用于真正 import 的运行时模块；这里既验真库应全绿，又用 monkeypatch 造反例证明
每条能抓到问题。软检查（apps/AX）不在这里跑真 subprocess。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from macos import preflight  # noqa: E402
from macos import actions as macos_actions  # noqa: E402
from macos import prompts as macos_prompts  # noqa: E402


def test_real_repo_hard_checks_all_pass():
    """真库：三条硬一致性检查必须全绿（否则 agent 会神秘卡死）。"""
    hard = {"whitelist↔keycode", "prompt↔whitelist", "ACTION_SPEC↔handlers"}
    got = {c.name: c for c in preflight.run() if c.name in hard}
    assert set(got) == hard
    for name, c in got.items():
        assert c.ok, f"{name} 挂了：{c.detail}"


def test_whitelist_keycode_catches_missing_keycode(monkeypatch):
    # 授权一个不存在的键 → _parse_key 返回 None → 该检查必须失败
    monkeypatch.setattr(macos_actions, "ALLOWED_COMBOS",
                        set(macos_actions.ALLOWED_COMBOS) | {"cmd+zzz"})
    c = preflight.check_whitelist_keycodes()
    assert c.ok is False and "cmd+zzz" in c.detail and c.hard is True


def test_prompt_keys_allowed_catches_untaught_key(monkeypatch):
    bad_prompt = "存文件请按 cmd+shift+p 保存"  # cmd+shift+p 不在白名单
    monkeypatch.setattr(macos_prompts, "get_system_prompt", lambda: bad_prompt)
    c = preflight.check_prompt_keys_allowed()
    assert c.ok is False and "cmd+shift+p" in c.detail


def test_action_spec_parity_catches_drift(monkeypatch):
    monkeypatch.setattr(macos_prompts, "ACTION_SPEC",
                        dict(macos_prompts.ACTION_SPEC, ghost_action={}))
    c = preflight.check_action_spec_parity()
    assert c.ok is False and "ghost_action" in c.detail


def _only_hard_checks(monkeypatch):
    """把 CHECKS 收窄到三条静态硬检查，避免 run()/main() 跑真 subprocess(open -Ra)/真 AX。"""
    monkeypatch.setattr(preflight, "CHECKS", [
        preflight.check_whitelist_keycodes,
        preflight.check_prompt_keys_allowed,
        preflight.check_action_spec_parity,
    ])


def test_hard_failures_and_report_shape(monkeypatch):
    _only_hard_checks(monkeypatch)
    monkeypatch.setattr(macos_actions, "ALLOWED_COMBOS",
                        set(macos_actions.ALLOWED_COMBOS) | {"cmd+zzz"})
    checks = preflight.run()
    assert preflight.hard_failures(checks)                     # 至少一条硬失败
    assert "❌" in preflight.format_report(checks)             # 报告里标了硬失败
    assert preflight.main() == 1                               # 退出码非 0


def test_clean_config_main_exit_zero(monkeypatch):
    _only_hard_checks(monkeypatch)                             # 真库配置本就干净
    assert preflight.main() == 0


def test_run_agent_check_flag_delegates_to_preflight(monkeypatch):
    """run_agent.py --check 只跑配置自检并返回它的退出码，不触发 VM 安全硬门。"""
    import run_agent
    monkeypatch.setattr(sys, "argv", ["run_agent.py", "--check"])
    monkeypatch.setattr("macos.preflight.main", lambda: 0)
    # 若误走了 _preflight() 安全硬门，会因非 VM 环境抛别的错；这里应干净返回 0
    assert run_agent.main() == 0


def test_run_agent_preflight_refuses_on_hard_config_failure(monkeypatch):
    """配置自检硬失败 → run_agent._preflight() 抛 SystemExit(5)（早退出，不带病启动）。"""
    import run_agent
    # 让安全硬门（clean-token/VM 守卫）先放行，单独验配置自检这一段
    monkeypatch.setattr(run_agent, "consume_clean_token", lambda: (True, ""))

    class _OkGuard:
        def refusal_reason(self):
            return ""
    monkeypatch.setattr(run_agent, "get_guard", lambda: _OkGuard())
    monkeypatch.setattr(run_agent, "weak_isolation_warnings", lambda: [])
    bad = preflight.Check("whitelist↔keycode", False, "坏", "修", hard=True)
    monkeypatch.setattr("macos.preflight.run", lambda: [bad])
    with pytest.raises(SystemExit) as ei:
        run_agent._preflight()
    assert ei.value.code == 5
