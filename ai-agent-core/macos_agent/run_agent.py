"""run_agent.py — VM 内入口（spec §7B）。

顺序（硬门在前，spec §2A.4 / §2A.1）：
  1) §2A.4 干净启动证明：消费一次性 token；证明不了 → 拒绝退出。
  2) §2A.1 VM 守卫：不在 VM（机型不在白名单 / 信号 B 缺）→ 拒绝退出。
  3) 建 MacSession + Planner(base_url=broker/v1, api_key=broker token,
     prompts_module=macos.prompts) + ReActAgent（四函数全注入）。
  4) 问任务 / 步数 → 跑循环。每步截图（screencapture -x，缺 Screen Recording
     则降级跳过不中止）+ transcript.jsonl。

不留宿主 watcher/轮询（spec §2A.4 末行 / A9）——本进程只跑在 guest 内。

环境变量：
  BROKER_BASE_URL   VM 端指向宿主 broker，如 http://192.168.66.1:8899/v1
  BROKER_TOKEN      broker 的 bearer token（**不是**真 DeepSeek key）
  DEEPSEEK_MODEL    可选，默认 deepseek-v4-flash
  MACOS_AGENT_OUT   审计产物目录，默认 ~/.macos_agent/runs
  MACOS_AGENT_SKIP_CLEAN_TOKEN=1   仅供受控演示：跳过 §2A.4 硬门（会大声打旗）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# 让 `python run_agent.py` 能直接 import 同级 brain / macos 包
sys.path.insert(0, str(Path(__file__).resolve().parent))

from macos import prompts as macos_prompts  # noqa: E402
from macos.actions import execute as mac_execute, dismiss_popups as mac_dismiss  # noqa: E402
from macos.health import assess_mac_health  # noqa: E402
from macos.observe import observe as mac_observe  # noqa: E402
from macos.session import MacSession  # noqa: E402
from macos.guard import get_guard, consume_clean_token  # noqa: E402
from brain.agent import ReActAgent  # noqa: E402
from brain.llm import Planner  # noqa: E402


def _out_dir() -> Path:
    d = Path(os.getenv("MACOS_AGENT_OUT", "~/.macos_agent/runs")).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _screenshot(path: Path) -> bool:
    """screencapture -x（静默截图）。缺 Screen Recording 授权 → 降级跳过、
    不中止（spec §3：Screen Recording 只为审计，缺它照样能操作）。"""
    try:
        proc = subprocess.run(
            ["screencapture", "-x", str(path)],
            capture_output=True, timeout=10,
        )
        return proc.returncode == 0 and path.exists()
    except Exception:
        return False


def _make_step_recorder(run_dir: Path):
    transcript_path = run_dir / "transcript.jsonl"
    shots_dir = run_dir / "screenshots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    fh = transcript_path.open("a", encoding="utf-8")
    state = {"screenshots": 0, "action_steps": 0}

    def on_step(entry: dict) -> None:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        fh.flush()
        # "步" = 带 action 的行（spec §7B）；只给这些行配截图，A6 才对得上
        if isinstance(entry, dict) and entry.get("action"):
            state["action_steps"] += 1
            shot = shots_dir / f"step_{entry.get('step', state['action_steps']):03d}.png"
            if _screenshot(shot):
                state["screenshots"] += 1

    return on_step, fh, state, transcript_path


def weak_isolation_warnings() -> list[str]:
    """§4A 最佳努力探针——只报 guest 内能诚实观察到的口子，**不宣称**验证了
    hypervisor 隔离（§2A.0#3：共享剪贴板/USB 等只能靠 hypervisor 配置关掉，
    被注入的 guest 无法自证隔离）。返回警告列表，空 = 没探到显性口子。

    做两件够得着且不误导的检查：
      1. 放代码的目录是否可写（§4A：代码挂载应只读——可写意味着被注入的
         guest 能改写 run_agent.py/broker.py 或塞 sitecustomize.py）。
      2. 代码目录里是否躺着 .env（A10：共享代码里不该有 .env）。
    """
    warnings: list[str] = []
    code_dir = Path(__file__).resolve().parent
    if os.access(code_dir, os.W_OK):
        warnings.append(
            f"代码目录 {code_dir} 可写——§4A 要求放代码的共享挂载设为只读"
            "（防被注入的 guest 改写 run_agent.py/broker.py）。"
        )
    for env_file in code_dir.glob(".env"):
        warnings.append(
            f"代码目录里发现 {env_file.name}——A10 要求共享代码不含 .env"
            "（真 key 只该在宿主 broker，绝不进 VM）。"
        )
    return warnings


def _preflight() -> None:
    """§2A.4 + §2A.1 硬门。任一不过 → 打印拒绝理由并退出（无 --force 旁路）。"""
    # 1) 干净启动证明（§2A.4）
    if os.getenv("MACOS_AGENT_SKIP_CLEAN_TOKEN") == "1":
        sys.stderr.write(
            "\n⚠️  [SECURITY DOWNGRADE] MACOS_AGENT_SKIP_CLEAN_TOKEN=1 —— "
            "§2A.4 一次性硬门被跳过。安全声明降级为：本 VM 长期存活、非一次性，"
            "guest 被注入视为持久感染。仅限受控演示使用。\n\n"
        )
    else:
        ok, reason = consume_clean_token()
        if not ok:
            sys.stderr.write(f"\n[REFUSED] {reason}\n")
            raise SystemExit(3)

    # 2) VM 守卫（§2A.1）——观察器是只读的可以任意环境跑，但改状态动作会
    #    被 actions 层的守卫逐一拦；这里做一次启动期总检查，早拒绝早退出。
    guard = get_guard()
    refusal = guard.refusal_reason()
    if refusal:
        sys.stderr.write(
            f"\n[REFUSED] {refusal}\n"
            "本代理只能在选定的 macOS 虚拟机内运行（spec §2A.1）。\n"
        )
        raise SystemExit(4)

    # 3) §4A 最佳努力隔离探针——软警告，不中止（这些是 hypervisor 配置项，
    #    不是硬门；探不到 ≠ 隔离已验证，见 spec §2A.0#3）。
    for w in weak_isolation_warnings():
        sys.stderr.write(f"⚠️  [§4A 隔离警告，best-effort，非硬门] {w}\n")


async def _run(goal: str, max_steps: int, default_app: str | None) -> int:
    base_url = os.getenv("BROKER_BASE_URL", "http://127.0.0.1:8899/v1")
    token = os.getenv("BROKER_TOKEN", "")
    if not token:
        sys.stderr.write(
            "[WARN] BROKER_TOKEN 为空——extract/decide 会打到没有认证的 broker。\n"
        )

    session = MacSession(guard=get_guard(), default_app=default_app)
    planner = Planner(
        base_url=base_url,
        api_key=token or None,
        prompts_module=macos_prompts,
    )
    agent = ReActAgent(
        session,
        planner,
        max_steps=max_steps,
        observe_fn=mac_observe,
        execute_fn=mac_execute,
        health_fn=assess_mac_health,
        dismiss_popups_fn=mac_dismiss,
    )

    run_dir = _out_dir() / time.strftime("run_%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    on_step, fh, state, transcript_path = _make_step_recorder(run_dir)

    print(f"\n▶ 任务: {goal}\n  步数上限: {max_steps}  审计: {run_dir}\n")
    try:
        # 桌面没有 URL 概念；start_url 只作 goal_ctx 里的"回家"锚点占位。
        result = await agent.run(goal, start_url="macos://desktop", on_step=on_step)
    finally:
        fh.close()

    print("\n" + "=" * 60)
    print(f"结果: {'✅ 成功' if result.success else '❌ 未达成'}  步数: {result.steps}")
    print(f"说明: {result.message}")
    if result.result is not None:
        print("数据:\n" + json.dumps(result.result, ensure_ascii=False, indent=2))
    print(f"审计: {transcript_path}  截图 {state['screenshots']}/{state['action_steps']} 步")
    print("=" * 60)

    # summary.json 供 A6 校验（截图数 == 带 action 行数）
    (run_dir / "summary.json").write_text(json.dumps({
        "goal": goal, "success": result.success, "steps": result.steps,
        "message": result.message, "result": result.result,
        "action_steps": state["action_steps"], "screenshots": state["screenshots"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    return 0 if result.success else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="macOS VM 桌面代理（模式四）")
    parser.add_argument("goal", nargs="?", help="任务目标（不给则交互式询问）")
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--app", default=None,
                        help="起点应用（会在开跑前带到前台，恢复时也用它）")
    args = parser.parse_args()

    _preflight()  # 硬门：不过直接退出

    goal = args.goal or input("任务目标: ").strip()
    if not goal:
        sys.stderr.write("没有任务目标，退出。\n")
        return 2

    return asyncio.run(_run(goal, args.max_steps, args.app))


if __name__ == "__main__":
    raise SystemExit(main())
