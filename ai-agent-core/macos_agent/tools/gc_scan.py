#!/usr/bin/env python3
"""Drift scanner for macos_agent harness hygiene."""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".pytest_cache", ".venv", "venv", "__pycache__"}


@dataclass
class Item:
    level: str
    file: str
    line: int
    title: str
    detail: str
    fixable: bool = False

    def markdown(self) -> str:
        loc = f"{self.file}:{self.line}"
        suffix = " (fixable)" if self.fixable else ""
        return f"- `{loc}` — **{self.title}**{suffix}: {self.detail}"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def rel(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def iter_py(root: Path, include_tests: bool = False):
    for path in sorted(root.rglob("*.py")):
        parts = set(path.relative_to(root).parts)
        if parts & SKIP_DIRS:
            continue
        if not include_tests and "tests" in parts:
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


def exception_keys(root: Path) -> set[tuple[str, str]]:
    return {
        (str(e.get("invariant", "")), str(e.get("file", "")))
        for e in parse_simple_yaml(root / "tools" / "known_exceptions.yaml")
    }


def scan_oversize(root: Path) -> list[Item]:
    known = exception_keys(root)
    items = []
    for path in iter_py(root, include_tests=False):
        n = len(read_text(path).splitlines())
        r = rel(path, root)
        if n > 500 and ("INV-01", r) not in known:
            items.append(Item("red", r, 1, "oversize file", f"{n} lines and not in known_exceptions.yaml"))
    return items


def missing_docstrings(root: Path) -> list[Item]:
    items = []
    for path in iter_py(root, include_tests=True):
        text = read_text(path)
        if path.name == "__init__.py" and not text.strip():
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        if ast.get_docstring(tree) is None:
            items.append(Item("green", rel(path, root), 1, "missing module docstring", "safe mechanical placeholder can be inserted", True))
    return items


def function_signature(node: ast.AST) -> str:
    clone = ast.parse(ast.unparse(node))
    for sub in ast.walk(clone):
        for attr in ("lineno", "col_offset", "end_lineno", "end_col_offset"):
            if hasattr(sub, attr):
                setattr(sub, attr, None)
        if isinstance(sub, ast.Name):
            sub.id = "_"
        elif isinstance(sub, ast.arg):
            sub.arg = "_"
    return ast.dump(clone, include_attributes=False)


def scan_duplicates(root: Path) -> list[Item]:
    buckets: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
    for path in iter_py(root, include_tests=False):
        try:
            tree = ast.parse(read_text(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body_len = sum(1 for _ in ast.walk(node))
            if body_len < 35:
                continue
            buckets[function_signature(node)].append((rel(path, root), node.lineno, node.name))
    items = []
    for matches in buckets.values():
        files = {m[0] for m in matches}
        if len(files) < 2:
            continue
        first = matches[0]
        others = ", ".join(f"{f}:{line} ({name})" for f, line, name in matches[1:4])
        items.append(Item("yellow", first[0], first[1], "possible duplicate implementation", f"similar function body also appears at {others}"))
        if len(items) >= 10:
            break
    return items


def scan_expired_exceptions(root: Path) -> list[Item]:
    today = dt.date.today()
    items = []
    for entry in parse_simple_yaml(root / "tools" / "known_exceptions.yaml"):
        review = str(entry.get("review_by", ""))
        try:
            review_date = dt.date.fromisoformat(review)
        except ValueError:
            items.append(Item("yellow", "tools/known_exceptions.yaml", 1, "exception review date invalid", f"{entry.get('file')} has review_by={review!r}"))
            continue
        if review_date < today:
            items.append(Item("yellow", "tools/known_exceptions.yaml", 1, "exception review overdue", f"{entry.get('invariant')} {entry.get('file')} review_by={review}"))
    return items


def default_allowed_apps(root: Path) -> set[str]:
    path = root / "macos" / "actions.py"
    if not path.exists():
        return set()
    try:
        tree = ast.parse(read_text(path))
    except SyntaxError:
        return set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "DEFAULT_ALLOWED_APPS" for t in node.targets):
            if isinstance(node.value, (ast.Tuple, ast.List)):
                return {elt.value for elt in node.value.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)}
    return set()


def env_vars_in_code(root: Path) -> set[str]:
    envs = set()
    for path in list(iter_py(root, include_tests=False)) + list((root / "scripts").glob("*.sh")):
        text = read_text(path)
        envs.update(re.findall(r'\bos\.getenv\("([A-Z0-9_]+)"', text))
        envs.update(re.findall(r'\bexport\s+([A-Z0-9_]+)=', text))
        envs.update(re.findall(r'\$\{?([A-Z][A-Z0-9_]+)(?::-[^}]*)?\}?', text))
    return {e for e in envs if e.startswith(("MACOS_AGENT_", "BROKER_", "DEEPSEEK_"))}


def actions_in_spec(root: Path) -> set[str]:
    path = root / "macos" / "prompts.py"
    if not path.exists():
        return set()
    try:
        tree = ast.parse(read_text(path))
    except SyntaxError:
        return set()
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "ACTION_SPEC":
            if isinstance(node.value, ast.Dict):
                return {k.value for k in node.value.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    return set()


def scan_doc_gardening(root: Path) -> list[Item]:
    setup = read_text(root / "SETUP.md") if (root / "SETUP.md").exists() else ""
    agents = read_text(root / "AGENTS.md") if (root / "AGENTS.md").exists() else ""
    items = []
    for app in sorted(default_allowed_apps(root)):
        if app not in setup:
            items.append(Item("yellow", "SETUP.md", 1, "launch_app whitelist doc drift", f"default app {app!r} is not documented in SETUP.md"))
    for env in sorted(env_vars_in_code(root)):
        if env not in setup:
            items.append(Item("yellow", "SETUP.md", 1, "environment variable doc drift", f"{env} appears in code but not SETUP.md"))
    combined = agents + "\n" + setup
    for action in sorted(actions_in_spec(root)):
        if action not in combined:
            items.append(Item("yellow", "AGENTS.md", 1, "action doc drift", f"ACTION_SPEC action {action!r} is not mentioned in AGENTS.md or SETUP.md"))
    return items


def apply_docstring_fixes(root: Path, items: list[Item]) -> list[str]:
    changed = []
    for item in items:
        if not (item.fixable and item.title == "missing module docstring"):
            continue
        path = root / item.file
        text = read_text(path)
        placeholder = f'"""TODO: describe {item.file} for agent context."""\n\n'
        path.write_text(placeholder + text, encoding="utf-8")
        changed.append(item.file)
    return changed


def mark_expired_exceptions(root: Path, items: list[Item]) -> list[str]:
    if not any(item.title == "exception review overdue" for item in items):
        return []
    path = root / "tools" / "known_exceptions.yaml"
    text = read_text(path)
    marker = "# gc_scan --fix: one or more known_exceptions review_by dates are overdue."
    if marker in text:
        return []
    path.write_text(text.rstrip() + "\n" + marker + "\n", encoding="utf-8")
    return ["tools/known_exceptions.yaml"]


def scan(root: Path = ROOT) -> list[Item]:
    items: list[Item] = []
    items.extend(scan_oversize(root))
    items.extend(missing_docstrings(root))
    items.extend(scan_duplicates(root))
    items.extend(scan_expired_exceptions(root))
    items.extend(scan_doc_gardening(root))
    return items


def render(items: list[Item], fixed: list[str] | None = None) -> str:
    fixed = fixed or []
    groups = {
        "red": ("🟥 High Risk", []),
        "yellow": ("🟨 Medium Risk", []),
        "green": ("🟩 Mechanical Safe", []),
    }
    for item in items:
        groups[item.level][1].append(item)
    lines = ["# macos_agent GC Scan", ""]
    for level in ("red", "yellow", "green"):
        title, group = groups[level]
        lines.extend([f"## {title}", ""])
        if group:
            lines.extend(item.markdown() for item in group)
        else:
            lines.append("- No findings.")
        lines.append("")
    if fixed:
        lines.append(f"Fixed mechanically safe items: {', '.join(fixed)}")
        lines.append("")
    lines.append("This report is a clue, not a verdict. 🟥/🟨 items need human judgment.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fix", action="store_true", help="apply only mechanically safe fixes")
    parser.add_argument("--root", default=str(ROOT), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    items = scan(root)
    fixed: list[str] = []
    if args.fix:
        fixed.extend(apply_docstring_fixes(root, items))
        fixed.extend(mark_expired_exceptions(root, items))
        items = scan(root)
    print(render(items, fixed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
