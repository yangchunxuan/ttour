#!/usr/bin/env python3
"""Lint the macos_agent knowledge base so agent-facing docs do not rot."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

FIXES = {
    "INV-KB-01": "AGENTS.md 是目录不是百科，把细节挪进 docs/",
    "INV-KB-02": "补上缺失文件或修链接",
    "INV-KB-03": "文档和 lint 的不变量清单漂移了：补齐缺的一边",
    "INV-KB-04": "引用了不存在的 §编号，修正或补章节",
}


@dataclass
class Finding:
    invariant_id: str
    file: str
    line: int
    problem: str
    fix: str

    def format(self) -> str:
        return (
            f"[KNOWLEDGE {self.invariant_id}] {self.file}:{self.line} — "
            f"{self.problem} → 修复：{self.fix}"
        )


def rel(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def line_of(text: str, needle: str) -> int:
    idx = text.find(needle)
    if idx < 0:
        return 1
    return text[:idx].count("\n") + 1


def markdown_files(root: Path) -> list[Path]:
    files = []
    agents = root / "AGENTS.md"
    if agents.exists():
        files.append(agents)
    docs = root / "docs"
    if docs.exists():
        files.extend(sorted(docs.glob("*.md")))
    return files


def check_agents_slim(root: Path) -> list[Finding]:
    path = root / "AGENTS.md"
    if not path.exists():
        return [Finding("INV-KB-01", "AGENTS.md", 1, "AGENTS.md 缺失", FIXES["INV-KB-01"])]
    n = len(read_text(path).splitlines())
    if n > 120:
        return [Finding("INV-KB-01", "AGENTS.md", 1, f"AGENTS.md 有 {n} 行，超过 120 行", FIXES["INV-KB-01"])]
    return []


def check_links(root: Path) -> list[Finding]:
    out = []
    link_re = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
    for path in markdown_files(root):
        text = read_text(path)
        for match in link_re.finditer(text):
            target = match.group(1).strip()
            if not target or re.match(r"^[a-z]+://", target) or target.startswith("#"):
                continue
            target = target.split("#", 1)[0]
            if not target:
                continue
            dest = (path.parent / target).resolve()
            try:
                dest.relative_to(root.resolve())
            except ValueError:
                out.append(Finding("INV-KB-02", rel(path, root), line_of(text, match.group(0)), f"链接越出子项目：{match.group(1)}", FIXES["INV-KB-02"]))
                continue
            if not dest.exists():
                out.append(Finding("INV-KB-02", rel(path, root), line_of(text, match.group(0)), f"链接目标不存在：{match.group(1)}", FIXES["INV-KB-02"]))
    return out


def implemented_invariants(root: Path) -> set[str]:
    src = read_text(root / "tools" / "lint_invariants.py")
    explicit = re.search(r"INVARIANT_IDS\s*=\s*tuple\(f\"INV-\{i:02d\}\".*?range\(1,\s*(\d+)\)", src, re.S)
    if explicit:
        return {f"INV-{i:02d}" for i in range(1, int(explicit.group(1)))}
    return set(re.findall(r"\bINV-\d{2}\b", src))


def check_invariant_sync(root: Path) -> list[Finding]:
    path = root / "docs" / "INVARIANTS.md"
    if not path.exists():
        return [Finding("INV-KB-03", "docs/INVARIANTS.md", 1, "INVARIANTS.md 缺失", FIXES["INV-KB-03"])]
    docs_ids = set(re.findall(r"\bINV-\d{2}\b", read_text(path)))
    code_ids = implemented_invariants(root)
    if docs_ids == code_ids:
        return []
    missing_docs = sorted(code_ids - docs_ids)
    missing_code = sorted(docs_ids - code_ids)
    problem = f"不变量清单漂移：docs 缺 {missing_docs}；lint 缺 {missing_code}"
    return [Finding("INV-KB-03", "docs/INVARIANTS.md", 1, problem, FIXES["INV-KB-03"])]


def spec_anchors(root: Path) -> set[str]:
    path = root / "docs" / "macos-desktop-agent-spec.md"
    if not path.exists():
        return set()
    anchors: set[str] = set()
    current = ""
    heading_re = re.compile(r"^#{2,3}\s+(\d+[A-Z]?)\.")
    bullet_re = re.compile(r"\*\*([A-Z]\d)(?:/([A-Z]\d))*\b")
    for line in read_text(path).splitlines():
        m = heading_re.match(line)
        if m:
            current = m.group(1)
            anchors.add(current)
        if current:
            for m in bullet_re.finditer(line):
                anchors.add(f"{current}-{m.group(1)}")
                if m.group(2):
                    anchors.add(f"{current}-{m.group(2)}")
    return anchors


def check_section_refs(root: Path) -> list[Finding]:
    anchors = spec_anchors(root)
    out = []
    # Validate cross-spec refs such as §7A-C3 and base refs such as §2A.3.2.
    ref_re = re.compile(r"§(\d+A(?:-[A-Z]\d+)?)(?:\.\d+)*")
    for path in markdown_files(root):
        text = read_text(path)
        for match in ref_re.finditer(text):
            ref = match.group(1)
            base = ref.split(".", 1)[0]
            if ref in anchors or base in anchors:
                continue
            out.append(Finding("INV-KB-04", rel(path, root), line_of(text, match.group(0)), f"§编号在 macos-desktop-agent-spec.md 中不存在：{match.group(0)}", FIXES["INV-KB-04"]))
    return out


CHECKS = [check_agents_slim, check_links, check_invariant_sync, check_section_refs]


def run(root: Path = ROOT) -> list[Finding]:
    findings: list[Finding] = []
    for check in CHECKS:
        findings.extend(check(root))
    findings.sort(key=lambda f: (f.invariant_id, f.file, f.line))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit structured findings")
    parser.add_argument("--root", default=str(ROOT), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    findings = run(Path(args.root).resolve())
    if args.json:
        print(json.dumps([asdict(f) for f in findings], ensure_ascii=False, indent=2))
    else:
        if not findings:
            print("lint_knowledge: ok")
        for finding in findings:
            print(finding.format())
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
