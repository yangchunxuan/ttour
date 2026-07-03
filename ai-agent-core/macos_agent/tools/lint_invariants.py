#!/usr/bin/env python3
"""Mechanical architecture invariants for the macos_agent harness."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
INVARIANT_IDS = tuple(f"INV-{i:02d}" for i in range(1, 15))

FIXES = {
    "INV-01": "拆分该文件为多个 <400 行模块，或若确因外部约束不能拆，登记 known_exceptions.yaml 并写理由",
    "INV-02": "把该 import 移进函数体的 try/except，macOS 路径强制注入实现（见总 spec §7A-C1/agent.py 懒加载）",
    "INV-03": "顶层出现了会拉起 Playwright 或缺失本地开发依赖的 import；按 §7A 改懒加载，并安装 openai/python-dotenv",
    "INV-04": "动作名在 prompts 和 actions 里对不上：补齐缺的 handler 或从 ACTION_SPEC 删掉；两边必须一致（总 spec §6）",
    "INV-05": "主 planner 和 extract 提示词必须都带数据/指令隔离前言（A11/§2A.5）；补上缺的那半边",
    "INV-06": "token 比较改 hmac.compare_digest，防计时侧信道（§2A.3.2）",
    "INV-07": "把 key 从代码里删掉；真 key 只经环境变量进宿主 broker，绝不进代码/VM（A7）",
    "INV-08": "机型用精确白名单集合判定，不要模糊子串；守卫无 --force 旁路（§2A.1）",
    "INV-09": "extract 必须把 base_url/api_key 穿透进 utils，否则绕过 broker 直打 api.deepseek.com（§7A-C3）",
    "INV-10": "rm -rf 目标前加路径守卫，防空变量/误删（utm_restore 的教训）",
    "INV-11": "补齐 agent.py 循环消费的字段，缺一个循环就崩（§5）",
    "INV-12": "给该模块加一句 docstring 说明它是干嘛的（agent 靠它建立上下文）",
    "INV-13": "系统提示词教模型按的组合键必须都在 press_key 白名单（ALLOWED_COMBOS）里；否则模型会被执行器拒绝、原地卡死。补进白名单或改提示词。",
    "INV-14": "保存到指定目录必须走 cmd+shift+g「前往文件夹」定目录 + 文件名框只填纯文件名（不带 /）；绝不能教模型把路径塞进文件名框（/ 触发 Go-to-Folder → 假成功，A2 教训）。",
}

SKIP_DIRS = {".git", ".pytest_cache", ".mypy_cache", ".venv", "venv", "__pycache__"}


@dataclass
class Finding:
    invariant_id: str
    file: str
    line: int
    problem: str
    fix: str
    exempt: bool = False
    reason: str = ""
    review_by: str = ""

    def format(self) -> str:
        msg = (
            f"[INVARIANT {self.invariant_id}] {self.file}:{self.line} — "
            f"{self.problem} → 修复：{self.fix}"
        )
        if self.exempt:
            return f"⚠️ 豁免中 {msg}（reason: {self.reason}; review_by: {self.review_by}）"
        return msg


def rel(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def iter_py(root: Path, include_tests: bool = True) -> Iterable[Path]:
    for path in sorted(root.rglob("*.py")):
        parts = set(path.relative_to(root).parts)
        if parts & SKIP_DIRS:
            continue
        if not include_tests and "tests" in parts:
            continue
        yield path


def iter_text_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        parts = set(path.relative_to(root).parts)
        if parts & SKIP_DIRS:
            continue
        if path.suffix in {".pyc", ".png", ".jpg", ".jpeg", ".gif", ".pdf"}:
            continue
        yield path


def parse_simple_yaml(path: Path) -> list[dict]:
    if not path.exists():
        return []
    items: list[dict] = []
    current: dict | None = None
    for raw in read_text(path).splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("- "):
            if current:
                items.append(current)
            current = {}
            rest = stripped[2:].strip()
            if rest and ":" in rest:
                key, val = rest.split(":", 1)
                current[key.strip()] = val.strip().strip("'\"")
        elif current is not None and ":" in stripped:
            key, val = stripped.split(":", 1)
            current[key.strip()] = val.strip().strip("'\"")
    if current:
        items.append(current)
    return items


def apply_exceptions(findings: list[Finding], root: Path) -> None:
    exceptions = parse_simple_yaml(root / "tools" / "known_exceptions.yaml")
    by_key = {(e.get("invariant"), e.get("file")): e for e in exceptions}
    for finding in findings:
        match = by_key.get((finding.invariant_id, finding.file))
        if match:
            finding.exempt = True
            finding.reason = str(match.get("reason", ""))
            finding.review_by = str(match.get("review_by", ""))


def ast_for(path: Path) -> ast.Module:
    return ast.parse(read_text(path), filename=str(path))


def check_inv_01(root: Path) -> list[Finding]:
    out = []
    for path in iter_py(root, include_tests=False):
        lines = read_text(path).splitlines()
        if len(lines) > 500:
            out.append(Finding("INV-01", rel(path, root), 1, f"文件 {len(lines)} 行，超过 500 行上限", FIXES["INV-01"]))
    return out


def check_inv_02(root: Path) -> list[Finding]:
    banned = ("playwright", "agent.dom", "agent.actions", "agent.browser")
    out = []
    for path in sorted((root / "brain").glob("*.py")):
        tree = ast_for(path)
        for node in tree.body:
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            for mod in modules:
                if any(mod == b or mod.startswith(b + ".") for b in banned):
                    out.append(Finding("INV-02", rel(path, root), node.lineno, f"brain 顶层 import 禁止模块 {mod!r}", FIXES["INV-02"]))
    return out


def check_inv_03(root: Path) -> list[Finding]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-c", "import brain.agent, brain.llm, brain.utils"],
        cwd=str(root),
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    if proc.returncode == 0:
        return []
    msg = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or ["import failed"]
    return [Finding("INV-03", "brain/agent.py", 1, f"A8 import 失败：{msg[0]}", FIXES["INV-03"])]


def import_from(root: Path, modules: list[str]) -> list[object]:
    prefixes = {m.split(".", 1)[0] for m in modules}
    saved_path = list(sys.path)
    saved_modules = {k: v for k, v in sys.modules.items() if k in prefixes or any(k.startswith(p + ".") for p in prefixes)}
    for key in list(saved_modules):
        sys.modules.pop(key, None)
    sys.path.insert(0, str(root))
    try:
        return [__import__(m, fromlist=["*"]) for m in modules]
    finally:
        for key in list(sys.modules):
            if key in prefixes or any(key.startswith(p + ".") for p in prefixes):
                sys.modules.pop(key, None)
        sys.modules.update(saved_modules)
        sys.path[:] = saved_path


def check_inv_04(root: Path) -> list[Finding]:
    try:
        prompts, actions = import_from(root, ["macos.prompts", "macos.actions"])
        spec = set(prompts.ACTION_SPEC)
        handlers = set(actions._HANDLERS) | {"wait", "done"}
    except Exception as exc:
        return [Finding("INV-04", "macos/prompts.py", 1, f"无法内省 ACTION_SPEC/_HANDLERS：{exc}", FIXES["INV-04"])]
    if spec == handlers:
        return []
    problem = f"ACTION_SPEC 与 handlers 不一致：prompts-only={sorted(spec - handlers)}, handlers-only={sorted(handlers - spec)}"
    return [Finding("INV-04", "macos/prompts.py", 1, problem, FIXES["INV-04"])]


def check_inv_05(root: Path) -> list[Finding]:
    out = []
    try:
        (prompts,) = import_from(root, ["macos.prompts"])
        if prompts.INJECTION_PREAMBLE not in prompts.get_system_prompt():
            out.append(Finding("INV-05", "macos/prompts.py", 1, "get_system_prompt() 未包含 INJECTION_PREAMBLE", FIXES["INV-05"]))
    except Exception as exc:
        out.append(Finding("INV-05", "macos/prompts.py", 1, f"无法内省主提示词：{exc}", FIXES["INV-05"]))
    utils = root / "brain" / "utils.py"
    src = read_text(utils) if utils.exists() else ""
    if "_EXTRACT_INJECTION_PREAMBLE" not in src or src.count("_EXTRACT_INJECTION_PREAMBLE") < 2:
        out.append(Finding("INV-05", "brain/utils.py", 1, "extract system prompt 未定义并使用 _EXTRACT_INJECTION_PREAMBLE", FIXES["INV-05"]))
    return out


def check_inv_06(root: Path) -> list[Finding]:
    path = root / "broker.py"
    src = read_text(path)
    out = []
    if "hmac.compare_digest" not in src:
        out.append(Finding("INV-06", "broker.py", 1, "bearer token 未使用 hmac.compare_digest", FIXES["INV-06"]))
    for i, line in enumerate(src.splitlines(), 1):
        if re.search(r"\bauth\s*==|==\s*expected\b|\bexpected\s*==", line) and "compare_digest" not in line:
            out.append(Finding("INV-06", "broker.py", i, "疑似裸比较 bearer token", FIXES["INV-06"]))
    return out


def check_inv_07(root: Path) -> list[Finding]:
    secret_re = re.compile(r"sk-[A-Za-z0-9]{20,}")
    out = []
    for path in iter_text_files(root):
        for i, line in enumerate(read_text(path).splitlines(), 1):
            if secret_re.search(line):
                out.append(Finding("INV-07", rel(path, root), i, "疑似硬编码 sk-* API key", FIXES["INV-07"]))
    return out


def check_inv_08(root: Path) -> list[Finding]:
    out = []
    guard = root / "macos" / "guard.py"
    if guard.exists():
        for node in ast.walk(ast_for(guard)):
            if isinstance(node, ast.Compare) and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
                text = ast.get_source_segment(read_text(guard), node) or ""
                if "Virtual" in text:
                    out.append(Finding("INV-08", "macos/guard.py", node.lineno, "机型判定疑似使用含 Virtual 的子串/成员模糊匹配", FIXES["INV-08"]))
    runtime_paths: list[Path] = []
    runtime_paths.extend(sorted((root / "brain").glob("*.py")))
    runtime_paths.extend(sorted((root / "macos").glob("*.py")))
    runtime_paths.extend(p for p in (root / "broker.py", root / "run_agent.py") if p.exists())
    runtime_paths.extend(sorted((root / "scripts").glob("*.sh")))
    for path in runtime_paths:
        doc_lines: set[int] = set()
        if path.suffix == ".py":
            try:
                tree = ast_for(path)
                for node in ast.walk(tree):
                    if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    body = getattr(node, "body", [])
                    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) and isinstance(body[0].value.value, str):
                        end = getattr(body[0], "end_lineno", body[0].lineno)
                        doc_lines.update(range(body[0].lineno, end + 1))
            except SyntaxError:
                pass
        for i, line in enumerate(read_text(path).splitlines(), 1):
            if i in doc_lines or line.strip().startswith("#"):
                continue
            if "--force" in line:
                out.append(Finding("INV-08", rel(path, root), i, "出现 --force，疑似守卫旁路", FIXES["INV-08"]))
    return out


def check_inv_09(root: Path) -> list[Finding]:
    path = root / "brain" / "llm.py"
    tree = ast_for(path)
    for cls in [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Planner"]:
        for fn in [n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "extract"]:
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "extract_information_json":
                    keys = {kw.arg for kw in node.keywords if kw.arg}
                    if {"base_url", "api_key"} <= keys:
                        return []
                    return [Finding("INV-09", "brain/llm.py", node.lineno, "Planner.extract 调用 extract_information_json 未传 base_url/api_key", FIXES["INV-09"])]
    return [Finding("INV-09", "brain/llm.py", 1, "找不到 Planner.extract -> extract_information_json 调用", FIXES["INV-09"])]


def _guarded(lines: list[str], idx: int, var: str) -> bool:
    window = "\n".join(lines[max(0, idx - 40):idx])
    guard_patterns = [
        rf'\[\[\s*"\${var}"\s*(?:==|!=)\s*\*\.utm\s*\]\]',
        rf'\[\[\s*-n\s*"\${var}"\s*\]\]',
        rf'\[\[\s*-z\s*"\${var}"\s*\]\].*exit',
        rf'case\s+"\${var}"\s+in',
    ]
    if any(re.search(p, window) for p in guard_patterns):
        return True
    assign = re.search(rf'^\s*{var}="?\$\{{?([A-Za-z_][A-Za-z0-9_]*)\}}?', window, re.MULTILINE)
    return bool(assign and _guarded(lines, idx, assign.group(1)))


def check_inv_10(root: Path) -> list[Finding]:
    out = []
    for path in sorted((root / "scripts").glob("*.sh")):
        lines = read_text(path).splitlines()
        for i, line in enumerate(lines, 1):
            for var in re.findall(r'rm\s+-rf\s+"\$([A-Za-z_][A-Za-z0-9_]*)"', line):
                if not _guarded(lines, i - 1, var):
                    out.append(Finding("INV-10", rel(path, root), i, f'rm -rf "${var}" 前缺少同块路径守卫', FIXES["INV-10"]))
    return out


def check_inv_11(root: Path) -> list[Finding]:
    try:
        (observe,) = import_from(root, ["macos.observe"])
        dom_fields = set(getattr(observe.MacDomState, "__dataclass_fields__", {}))
        el_fields = set(getattr(observe.MacElement, "__dataclass_fields__", {}))
        missing_dom = {"url", "title", "page_text", "elements"} - dom_fields
        missing_dom |= {m for m in {"get", "to_prompt"} if not hasattr(observe.MacDomState, m)}
        missing_el = {"index", "text", "attributes"} - el_fields
        missing_el |= {m for m in {"render"} if not hasattr(observe.MacElement, m)}
    except Exception as exc:
        return [Finding("INV-11", "macos/observe.py", 1, f"无法内省 MacDomState/MacElement：{exc}", FIXES["INV-11"])]
    if not missing_dom and not missing_el:
        return []
    return [Finding("INV-11", "macos/observe.py", 1, f"DomState 缺 {sorted(missing_dom)}；Element 缺 {sorted(missing_el)}", FIXES["INV-11"])]


def check_inv_12(root: Path) -> list[Finding]:
    out = []
    for path in iter_py(root, include_tests=True):
        if path.name == "__init__.py" and not read_text(path).strip():
            continue
        try:
            tree = ast_for(path)
        except SyntaxError as exc:
            out.append(Finding("INV-12", rel(path, root), exc.lineno or 1, f"Python 语法错误，无法读取 docstring：{exc.msg}", FIXES["INV-12"]))
            continue
        if ast.get_docstring(tree) is None:
            out.append(Finding("INV-12", rel(path, root), 1, "模块缺少顶层 docstring", FIXES["INV-12"]))
    return out


def check_inv_13(root: Path) -> list[Finding]:
    """系统提示词教模型按的组合键，必须都在 press_key 白名单里（否则教了个会被拒的键）。"""
    try:
        prompts, actions = import_from(root, ["macos.prompts", "macos.actions"])
        text = prompts.get_system_prompt()
    except Exception as exc:  # noqa: BLE001
        return [Finding("INV-13", "macos/prompts.py", 1, f"无法内省系统提示词/动作：{exc}", FIXES["INV-13"])]
    out, seen = [], set()
    for combo in re.findall(r"[A-Za-z]+(?:\+[A-Za-z]+)+", text):
        norm = combo.lower()
        if not norm.split("+")[0] in {"cmd", "command", "ctrl", "control", "opt", "option", "shift"}:
            continue  # 只校验修饰键组合，跳过非按键文本
        if norm in seen:
            continue
        seen.add(norm)
        if actions._parse_key(norm) is None:
            out.append(Finding("INV-13", "macos/prompts.py", 1,
                               f"提示词教模型按 {combo!r}，但它不在 press_key 白名单里（会被执行器拒绝）",
                               FIXES["INV-13"]))
    return out


def check_inv_14(root: Path) -> list[Finding]:
    """保存到指定目录：禁止「文件名框输路径」反模式；必须含 cmd+shift+g 定目录指引。"""
    try:
        (prompts,) = import_from(root, ["macos.prompts"])
        text = prompts.get_system_prompt()
    except Exception as exc:  # noqa: BLE001
        return [Finding("INV-14", "macos/prompts.py", 1, f"无法内省系统提示词：{exc}", FIXES["INV-14"])]
    out = []
    # 反模式：教模型「文件名框…可以…（完整）路径」（A2 假成功根因）
    if re.search(r"文件名[^。\n]{0,30}可以[^。\n]{0,12}(完整路径|路径)", text):
        out.append(Finding("INV-14", "macos/prompts.py", 1,
                           "保存指引疑似教模型往文件名框输入路径（/ 触发 Go-to-Folder → 假成功）",
                           FIXES["INV-14"]))
    # 正向要求：必须教「前往文件夹」定目录——go_to_folder 工具或 cmd+shift+g 任一即可
    low = text.lower()
    if "cmd+shift+g" not in low and "go_to_folder" not in low:
        out.append(Finding("INV-14", "macos/prompts.py", 1,
                           "保存指引缺少定目录的正确做法（go_to_folder 工具或 cmd+shift+g）", FIXES["INV-14"]))
    return out


CHECKS = [
    check_inv_01, check_inv_02, check_inv_03, check_inv_04,
    check_inv_05, check_inv_06, check_inv_07, check_inv_08,
    check_inv_09, check_inv_10, check_inv_11, check_inv_12,
    check_inv_13, check_inv_14,
]


def run(root: Path = ROOT) -> list[Finding]:
    findings: list[Finding] = []
    for check in CHECKS:
        findings.extend(check(root))
    findings.sort(key=lambda f: (f.invariant_id, f.file, f.line))
    apply_exceptions(findings, root)
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit structured findings")
    parser.add_argument("--root", default=str(ROOT), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    findings = run(root)
    if args.json:
        print(json.dumps([asdict(f) for f in findings], ensure_ascii=False, indent=2))
    else:
        if not findings:
            print("lint_invariants: ok")
        for finding in findings:
            print(finding.format())
    return 1 if any(not f.exempt for f in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
