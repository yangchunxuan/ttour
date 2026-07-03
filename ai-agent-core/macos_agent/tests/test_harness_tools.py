"""Tests for the harness governance tools.

The samples here are tiny temporary repositories. They prove each invariant can
catch a counterexample without touching the real runtime files.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import gc_scan  # noqa: E402
import lint_invariants as inv  # noqa: E402
import lint_knowledge as kb  # noqa: E402


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def ids(findings) -> set[str]:
    return {f.invariant_id for f in findings}


def test_inv_01_flags_oversize_file(tmp_path):
    write(tmp_path / "big.py", '"""doc"""\n' + "x = 1\n" * 501)
    assert "INV-01" in ids(inv.check_inv_01(tmp_path))


def test_inv_02_flags_brain_playwright_top_import(tmp_path):
    write(tmp_path / "brain" / "bad.py", '"""doc"""\nimport playwright.sync_api\n')
    assert "INV-02" in ids(inv.check_inv_02(tmp_path))


def test_inv_03_flags_failed_brain_import(tmp_path):
    write(tmp_path / "brain" / "__init__.py", "")
    write(tmp_path / "brain" / "agent.py", "import playwright\n")
    write(tmp_path / "brain" / "llm.py", "")
    write(tmp_path / "brain" / "utils.py", "")
    assert "INV-03" in ids(inv.check_inv_03(tmp_path))


def test_inv_04_flags_action_space_drift(tmp_path):
    write(tmp_path / "macos" / "__init__.py", "")
    write(tmp_path / "macos" / "prompts.py", 'ACTION_SPEC = {"click": {}, "wait": {}, "done": {}}\n')
    write(tmp_path / "macos" / "actions.py", "_HANDLERS = {}\n")
    assert "INV-04" in ids(inv.check_inv_04(tmp_path))


def test_inv_05_flags_missing_injection_preamble(tmp_path):
    write(tmp_path / "macos" / "__init__.py", "")
    write(tmp_path / "macos" / "prompts.py", 'INJECTION_PREAMBLE = "x"\ndef get_system_prompt():\n    return "no preamble"\n')
    write(tmp_path / "brain" / "utils.py", '"""doc"""\n')
    assert "INV-05" in ids(inv.check_inv_05(tmp_path))


def test_inv_06_flags_naked_broker_token_compare(tmp_path):
    write(tmp_path / "broker.py", '"""doc"""\ndef ok(auth, expected):\n    return auth == expected\n')
    assert "INV-06" in ids(inv.check_inv_06(tmp_path))


def test_inv_07_flags_hardcoded_secret(tmp_path):
    write(tmp_path / "scripts" / "bad.sh", "echo " + "sk-" + ("A" * 24) + "\n")
    assert "INV-07" in ids(inv.check_inv_07(tmp_path))


def test_inv_08_flags_virtual_substring_guard(tmp_path):
    write(tmp_path / "macos" / "guard.py", '"""doc"""\ndef is_vm(model):\n    return "Virtual" in model\n')
    assert "INV-08" in ids(inv.check_inv_08(tmp_path))


def test_inv_09_flags_extract_without_broker_args(tmp_path):
    write(tmp_path / "brain" / "llm.py", '''
class Planner:
    async def extract(self, page_text, schema):
        return await extract_information_json(page_text, schema, model=self.model)
''')
    assert "INV-09" in ids(inv.check_inv_09(tmp_path))


def test_inv_10_flags_rm_rf_without_guard(tmp_path):
    write(tmp_path / "scripts" / "bad.sh", '#!/bin/bash\nrm -rf "$TARGET"\n')
    assert "INV-10" in ids(inv.check_inv_10(tmp_path))


def test_inv_11_flags_domstate_contract_drift(tmp_path):
    write(tmp_path / "macos" / "__init__.py", "")
    write(tmp_path / "macos" / "observe.py", '''
from dataclasses import dataclass
@dataclass
class MacDomState:
    title: str = ""
class MacElement:
    pass
''')
    assert "INV-11" in ids(inv.check_inv_11(tmp_path))


def test_inv_12_flags_missing_module_docstring(tmp_path):
    write(tmp_path / "plain.py", "x = 1\n")
    assert "INV-12" in ids(inv.check_inv_12(tmp_path))


def test_inv_13_flags_prompt_key_not_in_allowlist(tmp_path):
    write(tmp_path / "macos" / "__init__.py", "")
    write(tmp_path / "macos" / "actions.py",
          '"""doc"""\nALLOWED_COMBOS = {"cmd+s"}\n'
          'def _parse_key(key):\n'
          '    return {"k": key} if key.lower() in ALLOWED_COMBOS else None\n')
    write(tmp_path / "macos" / "prompts.py",
          '"""doc"""\ndef get_system_prompt():\n'
          '    return "存文件用 cmd+shift+q 保存"\n')  # cmd+shift+q 不在白名单
    assert "INV-13" in ids(inv.check_inv_13(tmp_path))


def test_inv_13_catches_spaced_and_digit_combo(tmp_path):
    # 带空格 + 数字结尾的组合键（cmd + 1）也要能被抓到（评审 #9：旧正则漏扫）
    write(tmp_path / "macos" / "__init__.py", "")
    write(tmp_path / "macos" / "actions.py",
          '"""doc"""\nALLOWED_SINGLE_KEYS = {"enter"}\nALLOWED_COMBOS = {"cmd+s"}\n'
          'def _parse_key(key):\n'
          '    norm = "".join(key.lower().split())\n'
          '    return {"k": 1} if norm in {"cmd+s"} else None\n')
    write(tmp_path / "macos" / "prompts.py",
          '"""doc"""\ndef get_system_prompt():\n    return "切换请按 cmd + 1 键"\n')
    assert "INV-13" in ids(inv.check_inv_13(tmp_path))


def test_inv_13_ok_when_all_prompt_keys_allowed(tmp_path):
    write(tmp_path / "macos" / "__init__.py", "")
    write(tmp_path / "macos" / "actions.py",
          '"""doc"""\nALLOWED_COMBOS = {"cmd+s"}\n'
          'def _parse_key(key):\n'
          '    return {"k": key} if key.lower() in ALLOWED_COMBOS else None\n')
    write(tmp_path / "macos" / "prompts.py",
          '"""doc"""\ndef get_system_prompt():\n    return "存文件用 cmd+s 保存"\n')
    assert "INV-13" not in ids(inv.check_inv_13(tmp_path))


def _prompt_repo(tmp_path, body: str) -> None:
    write(tmp_path / "macos" / "__init__.py", "")
    write(tmp_path / "macos" / "prompts.py",
          f'"""doc"""\ndef get_system_prompt():\n    return {body!r}\n')


def test_inv_14_flags_path_in_filename_field(tmp_path):
    # 含正确的 cmd+shift+g 指引（正向分支过）→ 只有「文件名框可输路径」反模式分支该触发
    _prompt_repo(tmp_path, "用 cmd+shift+g 定目录；在文件名输入框可以直接输入完整路径。")
    assert "INV-14" in ids(inv.check_inv_14(tmp_path))


def test_inv_14_flags_missing_directory_guidance(tmp_path):
    # 无反模式、也无 go_to_folder/cmd+shift+g → 只有「缺定目录正确做法」分支该触发
    _prompt_repo(tmp_path, "存文件时点存储按钮即可。")
    assert "INV-14" in ids(inv.check_inv_14(tmp_path))


def test_inv_14_ok_on_compliant_prompt(tmp_path):
    # 有 cmd+shift+g 定目录、且明确禁止文件名框输路径（含「不可以」否定句不该被误伤）
    _prompt_repo(tmp_path, "用 cmd+shift+g 定目录；文件名框只填纯文件名不带斜杠，"
                           "文件名里不可以放完整路径。")
    assert "INV-14" not in ids(inv.check_inv_14(tmp_path))


def test_inv_15_flags_allowlisted_key_without_keycode(tmp_path):
    write(tmp_path / "macos" / "__init__.py", "")
    write(tmp_path / "macos" / "actions.py",
          '"""doc"""\nALLOWED_SINGLE_KEYS = {"enter"}\nALLOWED_COMBOS = {"cmd+zzz"}\n'
          'def _parse_key(key):\n'
          '    return {"k": 1} if key == "enter" else None\n')  # cmd+zzz 无键码 → None
    assert "INV-15" in ids(inv.check_inv_15(tmp_path))


def test_inv_15_ok_when_every_key_parses(tmp_path):
    write(tmp_path / "macos" / "__init__.py", "")
    write(tmp_path / "macos" / "actions.py",
          '"""doc"""\nALLOWED_SINGLE_KEYS = {"enter"}\nALLOWED_COMBOS = {"cmd+s"}\n'
          'def _parse_key(key):\n    return {"k": 1}\n')
    assert "INV-15" not in ids(inv.check_inv_15(tmp_path))


def test_lint_knowledge_flags_broken_link_and_invariant_drift(tmp_path):
    write(tmp_path / "AGENTS.md", "[broken](docs/missing.md)\n")
    write(tmp_path / "docs" / "INVARIANTS.md", "# INV-01 only\n")
    write(tmp_path / "docs" / "macos-desktop-agent-spec.md", "## 7A. Brain\n- **C1 ok**\n")
    write(tmp_path / "tools" / "lint_invariants.py", 'INVARIANT_IDS = ("INV-01", "INV-02")\n')
    found = ids(kb.run(tmp_path))
    assert "INV-KB-02" in found
    assert "INV-KB-03" in found


def test_gc_fix_adds_only_missing_docstring_placeholder(tmp_path):
    write(tmp_path / "plain.py", "x = 1\n")
    items = gc_scan.scan(tmp_path)
    fixed = gc_scan.apply_docstring_fixes(tmp_path, items)
    assert fixed == ["plain.py"]
    assert (tmp_path / "plain.py").read_text(encoding="utf-8").startswith('"""TODO:')


def test_gc_doc_gardening_flags_new_allowed_app_not_in_setup(tmp_path):
    write(tmp_path / "macos" / "actions.py", 'DEFAULT_ALLOWED_APPS = ("TextEdit", "NewApp")\n')
    write(tmp_path / "macos" / "prompts.py", 'ACTION_SPEC = {"launch_app": {}}\n')
    write(tmp_path / "SETUP.md", "TextEdit only\n")
    write(tmp_path / "AGENTS.md", "launch_app\n")
    findings = gc_scan.scan_doc_gardening(tmp_path)
    assert any("NewApp" in item.detail for item in findings)
