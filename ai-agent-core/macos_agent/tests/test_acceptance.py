"""tests/test_acceptance.py — 验收清单纯逻辑（离线，注入假 runner）。

重点证明：判定只看真值、不听 agent 自称——尤其 agent 说成功但产物不存在时，
场景必须判「假成功」而非通过（这正是 A2 栽过、本 harness 要机械根除的坑）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import acceptance as acc  # noqa: E402
from acceptance import (PathCond, ResultCond, Scenario, check_post,  # noqa: E402
                        run_scenarios)


# ---- 后置条件真值检查 ---- #
def test_pathcond_file_with_contains(tmp_path):
    f = tmp_path / "a.txt"; f.write_text("hello from agent", encoding="utf-8")
    ok, _ = check_post(PathCond(str(f), kind="file", contains="hello from agent"), {})
    assert ok is True
    bad, _ = check_post(PathCond(str(f), kind="file", contains="nope"), {})
    assert bad is False


def test_pathcond_missing_file_fails(tmp_path):
    ok, _ = check_post(PathCond(str(tmp_path / "nope.txt"), kind="file"), {})
    assert ok is False


def test_pathcond_dir_kind_enforced(tmp_path):
    f = tmp_path / "f.txt"; f.write_text("x", encoding="utf-8")
    # 存在但是文件、要求是目录 → 不满足
    ok, _ = check_post(PathCond(str(f), kind="dir"), {})
    assert ok is False
    d = tmp_path / "d"; d.mkdir()
    ok2, _ = check_post(PathCond(str(d), kind="dir"), {})
    assert ok2 is True


def test_resultcond_checks_agent_result():
    ok, _ = check_post(ResultCond("408"), {"result": {"result": 408}, "message": "算完了"})
    assert ok is True
    miss, _ = check_post(ResultCond("408"), {"result": {"result": 407}, "message": ""})
    assert miss is False


# ---- run_scenarios：真值判定 + 假成功识别 ---- #
def _scn(tmp_path):
    return Scenario(id="T", app="TextEdit",
                    goal="写文件", post=PathCond(str(tmp_path / "out.txt"),
                                                 kind="file", contains="DONE"))


def test_true_pass_when_artifact_real(tmp_path):
    target = tmp_path / "out.txt"

    def runner(s):
        target.write_text("... DONE ...", encoding="utf-8")  # 真造出产物
        return {"success": True, "message": "done", "result": None}

    (r,) = run_scenarios(runner, scenarios=[_scn(tmp_path)])
    assert r["truly_passed"] is True and r["false_success"] is False


def test_false_success_caught_when_artifact_absent(tmp_path):
    # agent 自称成功，但根本没造出文件 → 必须判假成功，绝不算过
    def runner(s):
        return {"success": True, "message": "我存好了！", "result": None}

    (r,) = run_scenarios(runner, scenarios=[_scn(tmp_path)])
    assert r["claimed_success"] is True
    assert r["truly_passed"] is False
    assert r["false_success"] is True


def test_honest_failure_not_flagged_as_false_success(tmp_path):
    def runner(s):
        return {"success": False, "message": "没做到", "result": None}

    (r,) = run_scenarios(runner, scenarios=[_scn(tmp_path)])
    assert r["truly_passed"] is False and r["false_success"] is False


def test_only_filter_selects_subset():
    seen = []

    def runner(s):
        seen.append(s.id)
        return {"success": False, "message": "", "result": None}

    run_scenarios(runner, only={"A4"})
    assert seen == ["A4"]


def test_report_flags_false_success_loudly(tmp_path):
    def runner(s):
        return {"success": True, "message": "假装成功", "result": None}

    results = run_scenarios(runner, scenarios=[_scn(tmp_path)])
    assert "假成功" in acc.format_report(results)


def test_default_scenarios_have_ground_truth_postconditions():
    # 每个内置场景都必须带真值判据（否则验收退化成听 agent 自称）
    assert acc.SCENARIOS
    for s in acc.SCENARIOS:
        assert isinstance(s.post, (PathCond, ResultCond))
