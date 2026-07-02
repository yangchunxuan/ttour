"""tests/test_macos_actions.py — macOS VM 代理单测。

不需要真 pyobjc / 真 VM / 真 DeepSeek：
  * AX 调用全在 macos.ax 里，测试 monkeypatch 它 → 观察器/执行器脱离真 AX。
  * 守卫的 system_profiler 调用通过 runner 注入 → 任意机型/异常可复现。
  * Planner 只测校验/修复的纯逻辑，不打网络。

跑：cd macos_agent && python3 -m pytest tests/ -q
（无 pytest 时可 `python3 tests/test_macos_actions.py` 跑内置断言 harness。）
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from macos import guard as guard_mod  # noqa: E402
from macos import prompts as macos_prompts  # noqa: E402
from macos.actions import execute, ActionResult, _parse_key  # noqa: E402
from macos.observe import MacDomState, MacElement  # noqa: E402
from brain.llm import Planner  # noqa: E402


# ------------------------------------------------------------------ #
# 测试替身
# ------------------------------------------------------------------ #

def _vm_runner(_cmd, _timeout):
    """假装在 VM 里：机型串命中白名单。"""
    return "      Model Identifier: VirtualMac2,1\n"

def _real_mac_runner(_cmd, _timeout):
    return "      Model Identifier: Mac17,3\n"

def _throwing_runner(_cmd, _timeout):
    raise RuntimeError("system_profiler exploded")


def _fake_guard(runner, secret_ok=True, tmp_path=None):
    """造一个 VmGuard：机型 runner 可注入，信号 B 用临时 root-ish 文件模拟。

    真实 A1 里信号 B 要 root 拥有 + 0600；单测环境不是 root，故把
    _signal_b_reason monkeypatch 成可控返回，专注测机型判定 + fail-closed 结构。
    """
    g = guard_mod.VmGuard(runner=runner)
    g._signal_b_reason = (lambda: "" if secret_ok else "signal B failed: secret missing")  # type: ignore
    return g


class _FakeSession:
    def __init__(self, guard):
        self.guard = guard


def _run(coro):
    # asyncio.run 每次建新循环——3.12+ 下 get_event_loop() 无运行循环会报错，
    # 且测试间彼此隔离更干净。
    return asyncio.run(coro)


# ================================================================== #
# A1：守卫 fail-closed（spec §8 A1 —— 含"守卫抛异常→拒绝"）
# ================================================================== #

def test_a1_vm_model_allows():
    g = _fake_guard(_vm_runner)
    assert g.refusal_reason() == ""
    assert g.is_vm() is True

def test_a1_real_mac_refused():
    g = _fake_guard(_real_mac_runner)
    assert g.refusal_reason() != ""
    assert "not in the VM whitelist" in g.refusal_reason()

def test_a1_signal_b_missing_refused():
    g = _fake_guard(_vm_runner, secret_ok=False)
    assert "signal B failed" in g.refusal_reason()

def test_a1_guard_exception_refused():
    """spec 明文要求：守卫抛异常 → 动作被拒。"""
    g = _fake_guard(_throwing_runner)
    reason = g.refusal_reason()
    assert reason != ""
    assert "cannot prove VM" in reason

def test_a1_state_action_refused_on_real_mac():
    """真 Mac 上跑改状态动作 → execute 拒绝（没碰 AX）。"""
    g = _fake_guard(_real_mac_runner)
    sess = _FakeSession(g)
    dom = MacDomState(elements=[MacElement(index=0, role="AXButton", text="OK")])
    res = _run(execute(sess, dom, {"name": "click", "args": {"index": 0}}, None))
    assert res.ok is False
    assert "cannot prove VM" in res.message

def test_a1_extract_is_guarded():
    """extract 非只读（经 broker egress），必须过守卫（spec §2A.2）。"""
    g = _fake_guard(_real_mac_runner)
    sess = _FakeSession(g)
    dom = MacDomState(page_text="some data", elements=[])
    res = _run(execute(sess, dom, {"name": "extract", "args": {"schema": "{}"}}, None))
    assert res.ok is False
    assert "cannot prove VM" in res.message

def test_a1_wait_and_done_are_inert():
    """wait/done 真 inert：即使在真 Mac 上也不过守卫（spec §2A.2）。"""
    g = _fake_guard(_real_mac_runner)
    sess = _FakeSession(g)
    dom = MacDomState()
    res_wait = _run(execute(sess, dom, {"name": "wait", "args": {"seconds": 0}}, None))
    assert res_wait.ok is True
    res_done = _run(execute(sess, dom, {"name": "done",
                    "args": {"success": True, "message": "ok"}}, None))
    assert res_done.done is True and res_done.ok is True

def test_a1_action_refusal_helper():
    g = _fake_guard(_real_mac_runner)
    assert guard_mod.action_refusal("wait", g) == ""
    assert guard_mod.action_refusal("done", g) == ""
    assert guard_mod.action_refusal("click", g) != ""


# ================================================================== #
# §2A.4 干净启动 token
# ================================================================== #

def test_clean_token_consumed_once(tmp_path):
    tok = tmp_path / "clean_token"
    tok.write_text("x")
    ok1, r1 = guard_mod.consume_clean_token(tok)
    assert ok1 is True and r1 == ""
    ok2, r2 = guard_mod.consume_clean_token(tok)
    assert ok2 is False and "missing" in r2

def test_clean_token_missing_refused(tmp_path):
    ok, reason = guard_mod.consume_clean_token(tmp_path / "nope")
    assert ok is False and "clean-start proof failed" in reason


# ================================================================== #
# press_key 白名单（spec §6：allowlist 非安全边界，但要挡系统级键）
# ================================================================== #

def test_press_key_allows_whitelisted():
    assert _parse_key("Enter") is not None
    assert _parse_key("Escape") is not None
    assert _parse_key("cmd+s") is not None

def test_press_key_blocks_spotlight_and_switcher():
    assert _parse_key("cmd+space") is None   # Spotlight
    assert _parse_key("cmd+tab") is None      # App 切换
    assert _parse_key("cmd+q") is None        # 退出

def test_press_key_action_blocked_in_vm(monkeypatch):
    """白名单外的键即使在 VM 里也被执行器拒（不碰 CGEvent）。"""
    g = _fake_guard(_vm_runner)
    sess = _FakeSession(g)
    dom = MacDomState()
    res = _run(execute(sess, dom, {"name": "press_key", "args": {"key": "cmd+space"}}, None))
    assert res.ok is False and "allowlist" in res.message


# ================================================================== #
# launch_app 白名单
# ================================================================== #

def test_launch_app_blocks_unlisted():
    g = _fake_guard(_vm_runner)
    sess = _FakeSession(g)
    dom = MacDomState()
    res = _run(execute(sess, dom, {"name": "launch_app", "args": {"app": "Terminal"}}, None))
    assert res.ok is False and "whitelist" in res.message


# ================================================================== #
# MacDomState / MacElement 契约（agent.py 循环实际消费的字段，spec §5）
# ================================================================== #

def test_domstate_contract():
    els = [
        MacElement(index=0, role="AXButton", title="Save", text="Save"),
        MacElement(index=1, role="AXTextField", text="", value="hello"),
    ]
    dom = MacDomState(title="TextEdit", page_text="body text here", elements=els,
                      app_name="TextEdit")
    # get(index) 一致
    assert dom.get(0).text == "Save"
    assert dom.get(99) is None
    # render() 不崩（_auto_extract_if_possible 每元素调）
    assert "[0]" in dom.get(0).render()
    # to_prompt() 含控件与文本
    p = dom.to_prompt()
    assert "Save" in p and "body text here" in p
    # url 带随焦点变的信号占位（此处构造未给，默认值也可 fingerprint）
    assert isinstance(dom.url, str)

def test_element_text_never_raises():
    """循环用 el.text 建签名；缺 text 会抛。确保默认值存在。"""
    el = MacElement(index=0)
    assert el.text == ""
    assert el.render()  # 不抛


# ================================================================== #
# A8：大脑可导入 + Planner 校验/修复（不打网络）
# ================================================================== #

def test_a8_brain_imports_without_playwright():
    """在无 Playwright 环境 import brain.agent / llm / utils 不报错。"""
    import brain.agent  # noqa: F401
    import brain.llm  # noqa: F401
    import brain.utils  # noqa: F401

def test_planner_requires_prompts_module():
    """§7A-C2：brain 包无默认提示词，prompts_module 必填。"""
    with pytest.raises(ValueError):
        Planner(prompts_module=None)

def _planner():
    return Planner(base_url="http://127.0.0.1:8899/v1", api_key="tok",
                   prompts_module=macos_prompts)

def test_planner_repairs_launch_app_alias():
    p = _planner()
    out = p._validate_and_repair({"thought": "open it",
                                  "action": {"name": "open", "args": {"app": "TextEdit"}}}, "{}")
    assert out["action"]["name"] == "launch_app"

def test_planner_rejects_webonly_action_gracefully():
    """go_to/switch_tab 不在桌面动作空间——修复层应降级为 wait，不 KeyError。"""
    p = _planner()
    out = p._validate_and_repair({"thought": "nav",
                                  "action": {"name": "go_to", "args": {"url": "http://x"}}}, "{}")
    # go_to 的 alias 目标是 go_to，但它不在 ACTION_NAMES → fallback wait
    assert out["action"]["name"] == "wait"
    assert "_repair" in out

def test_planner_coerces_string_index():
    p = _planner()
    out = p._validate_and_repair({"thought": "click",
                                  "action": {"name": "click", "args": {"index": "3"}}}, "{}")
    assert out["action"]["args"]["index"] == 3


# ================================================================== #
# A11 静态半：主 planner + extract 提示词都含注入隔离前言
# ================================================================== #

def test_a11_injection_preamble_present():
    from macos.prompts import get_system_prompt, INJECTION_PREAMBLE
    from brain.utils import _EXTRACT_INJECTION_PREAMBLE
    assert INJECTION_PREAMBLE in get_system_prompt()
    assert "不可信" in _EXTRACT_INJECTION_PREAMBLE
    assert "数据" in _EXTRACT_INJECTION_PREAMBLE


# ================================================================== #
# broker：constant-time token 比较 + schema 收紧（不打网络）
# ================================================================== #

def test_broker_uses_constant_time_token_compare():
    import broker
    # 结构性断言：_authorized 用 hmac.compare_digest，而不是裸 ==
    import inspect
    src = inspect.getsource(broker.BrokerHandler._authorized)
    assert "compare_digest" in src, "bearer token 比较必须 constant-time（§2A.3.2）"

def test_broker_sanitize_rejects_bad_model():
    import broker
    out, reason = broker.BrokerHandler._sanitize(
        {"model": "gpt-4o", "messages": [{"role": "user", "content": "x"}]})
    assert reason and "allowlist" in reason

def test_broker_sanitize_caps_max_tokens_and_refuses_stream():
    import broker
    out, reason = broker.BrokerHandler._sanitize({
        "model": "deepseek-v4-flash",
        "messages": [{"role": "user", "content": "x"}],
        "max_tokens": 10_000_000,
    })
    assert reason == "" and out["max_tokens"] <= broker.MAX_TOKENS_CAP
    out2, reason2 = broker.BrokerHandler._sanitize({
        "model": "deepseek-v4-flash",
        "messages": [{"role": "user", "content": "x"}],
        "stream": True,
    })
    assert reason2 and "streaming" in reason2


# ================================================================== #
# §4A best-effort 隔离探针（A10 非 token 条款的支撑代码）
# ================================================================== #

def test_weak_isolation_probe_flags_stray_env(monkeypatch, tmp_path):
    import run_agent
    # 把探针指向一个含 .env 的临时目录
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=sk-should-not-be-here")
    monkeypatch.setattr(run_agent, "__file__", str(tmp_path / "run_agent.py"))
    warns = run_agent.weak_isolation_warnings()
    assert any(".env" in w for w in warns)


# ------------------------------------------------------------------ #
# 无 pytest 时的兜底 harness
# ------------------------------------------------------------------ #
if __name__ == "__main__":
    import types
    self_mod = sys.modules[__name__]
    failures = 0
    passed = 0
    for attr in dir(self_mod):
        if not attr.startswith("test_"):
            continue
        fn = getattr(self_mod, attr)
        if not isinstance(fn, types.FunctionType):
            continue
        # 跳过需要 pytest fixture 的用例
        argcount = fn.__code__.co_argcount
        try:
            if argcount == 0:
                fn()
            elif "tmp_path" in fn.__code__.co_varnames[:argcount]:
                import tempfile
                with tempfile.TemporaryDirectory() as d:
                    fn(Path(d))
            elif "monkeypatch" in fn.__code__.co_varnames[:argcount]:
                fn(None)
            else:
                continue
            passed += 1
            print(f"  ✓ {attr}")
        except Exception as e:  # noqa: BLE001
            failures += 1
            print(f"  ✗ {attr}: {e}")
    print(f"\n{passed} passed, {failures} failed")
    sys.exit(1 if failures else 0)
