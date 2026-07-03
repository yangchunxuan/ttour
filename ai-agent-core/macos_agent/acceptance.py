"""acceptance.py — agent 任务级验收清单（机械化「定义完成」，专抓假成功）。

每个场景 = {目标 goal + 产物的**真值后置条件** post}。跑一个场景 = 让 agent 真做这个
目标，然后**查真值**（文件真在不在、结果真对不对）判定过没过——而不是听 agent 自称。
核心价值：把 A2/A4 这类「重跑让它真过?」从人肉判断变成一键机械判定，并**专门标出
「agent 说成功但真值不符」的假成功**（false_success），这正是 A2 栽过的坑。

分层（可离线测 vs 需 VM）：
  - SCENARIOS / check_post / run_scenarios：纯逻辑，注入 runner 即可离线测。
  - vm_runner：真在 VM 里跑 run_agent.py 并读 summary.json——只在 VM 内有意义。

跑（在 VM 里）：
  python3 -m acceptance list                 # 看有哪些场景
  python3 -m acceptance run                   # 全跑，查真值出诚实报告
  python3 -m acceptance run --only A2,A4       # 只跑指定几个
后置条件真值检查复用 macos.workflows.probe_path（与 verify_path 动作同一份逻辑）。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Union

from macos.workflows import probe_path


# ---- 后置条件：产物的真值判据 ---- #
@dataclass
class PathCond:
    """文件系统真值：某路径必须存在（kind=file/dir/any），可选文件含某段文字。"""
    path: str
    kind: str = "any"                 # file | dir | any
    contains: Optional[str] = None


@dataclass
class ResultCond:
    """结果真值：agent 交回的 result/message 里必须出现某个串（如计算结果 '408'）。"""
    needle: str


PostCond = Union[PathCond, ResultCond]


@dataclass
class Scenario:
    id: str
    app: str
    goal: str
    post: PostCond


# A2/A3/A4：真机验收里失败/存疑的三项，给出机械化真值判据。
SCENARIOS: list[Scenario] = [
    Scenario(
        id="A2", app="TextEdit",
        goal="打开 TextEdit 新建文档，输入文字 hello from agent，"
             "保存到 ~/Desktop/agent_a2.txt（保存对话框里先用 go_to_folder 定到 ~/Desktop，"
             "文件名只填 agent_a2.txt）。存完用 verify_path 核实再 done。",
        post=PathCond(path="~/Desktop/agent_a2.txt", kind="file", contains="hello from agent"),
    ),
    Scenario(
        id="A3", app="Calculator",
        goal="用计算器计算 12 × 34，把结果读出来并作为 result 返回。",
        post=ResultCond(needle="408"),
    ),
    Scenario(
        id="A4", app="Finder",
        goal="在 Finder 的桌面(~/Desktop)下用 new_folder 新建一个名为 agent_a4 的文件夹，"
             "建完用 verify_path 核实再 done。",
        post=PathCond(path="~/Desktop/agent_a4", kind="dir"),
    ),
]


def check_post(post: PostCond, run_result: dict) -> tuple[bool, str]:
    """查后置条件真值，返回 (是否真过, 说明)。不看 agent 自称，只看真值。"""
    if isinstance(post, PathCond):
        ok, msg, res = probe_path(post.path, post.contains)
        if post.kind == "dir" and not res["is_dir"]:
            return False, f"{msg} → 要求是目录，未满足"
        if post.kind == "file" and not res["is_file"]:
            return False, f"{msg} → 要求是文件，未满足"
        return ok, msg
    if isinstance(post, ResultCond):
        hay = (json.dumps(run_result.get("result"), ensure_ascii=False)
               + " " + str(run_result.get("message", "")))
        ok = post.needle in hay
        return ok, f"结果里{'含' if ok else '不含'} {post.needle!r}"
    return False, f"未知后置条件类型 {type(post).__name__}"


def run_scenarios(runner: Callable[[Scenario], dict],
                  only: Optional[set[str]] = None,
                  scenarios: Optional[list[Scenario]] = None) -> list[dict]:
    """对每个场景：runner 跑出 {success,message,result} → 查真值 → 记录是否真过 + 是否假成功。"""
    scenarios = scenarios if scenarios is not None else SCENARIOS
    out: list[dict] = []
    for s in scenarios:
        if only and s.id not in only:
            continue
        rr = runner(s) or {}
        claimed = bool(rr.get("success"))
        truly, detail = check_post(s.post, rr)
        out.append({
            "id": s.id, "goal": s.goal,
            "claimed_success": claimed,     # agent 自称
            "truly_passed": truly,          # 真值判定
            "false_success": claimed and not truly,   # 自称成功但真值不符（A2 坑）
            "detail": detail,
        })
    return out


def format_report(results: list[dict]) -> str:
    lines = []
    for r in results:
        if r["truly_passed"]:
            mark = "✅ 真过"
        elif r["false_success"]:
            mark = "🚨 假成功（自称成功、真值不符）"
        else:
            mark = "❌ 未过"
        lines.append(f"{mark}  [{r['id']}] {r['detail']}")
    passed = sum(1 for r in results if r["truly_passed"])
    fake = sum(1 for r in results if r["false_success"])
    lines.append(f"—— 真过 {passed}/{len(results)}"
                 + (f"，其中假成功 {fake} 例（最危险）" if fake else ""))
    return "\n".join(lines)


# ---- 真机 runner：在 VM 里跑 run_agent.py 并读 summary.json（VM-only）---- #
def vm_runner(scenario: Scenario, max_steps: int = 25) -> dict:
    root = Path(__file__).resolve().parent
    out = Path(os.getenv("MACOS_AGENT_OUT", "~/.macos_agent/runs")).expanduser()
    before = set(out.glob("run_*/summary.json")) if out.exists() else set()
    try:
        subprocess.run(
            [sys.executable, "run_agent.py", scenario.goal, "--app", scenario.app,
             "--max-steps", str(max_steps)],
            cwd=str(root), timeout=max_steps * 30, check=False,
        )
    except Exception as e:  # noqa: BLE001
        return {"success": False, "message": f"run_agent 启动失败：{e}", "result": None}
    # 只认**本次新产出**的 summary，别误读上一个场景的旧文件（跑前快照对比）
    fresh = sorted(set(out.glob("run_*/summary.json")) - before) if out.exists() else []
    if not fresh:
        return {"success": False, "message": "本次 run 没产出 summary.json（可能启动即被守卫拒）",
                "result": None}
    data = json.loads(fresh[-1].read_text(encoding="utf-8"))
    return {"success": data.get("success"), "message": data.get("message", ""),
            "result": data.get("result")}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="agent 任务级验收（查真值，抓假成功）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="列出所有验收场景")
    pr = sub.add_parser("run", help="跑验收（需在 VM 里）")
    pr.add_argument("--only", default="", help="只跑这些 id，逗号分隔，如 A2,A4")
    pr.add_argument("--max-steps", type=int, default=25)
    args = ap.parse_args(argv)

    if args.cmd == "list":
        for s in SCENARIOS:
            print(f"[{s.id}] app={s.app}\n    目标：{s.goal}\n    判据：{s.post}")
        return 0

    only = {x.strip() for x in args.only.split(",") if x.strip()} or None
    results = run_scenarios(lambda s: vm_runner(s, args.max_steps), only=only)
    print(format_report(results))
    # 真过才算过；有假成功一定非 0（最该报警）
    return 0 if results and all(r["truly_passed"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
