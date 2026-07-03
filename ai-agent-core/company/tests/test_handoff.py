"""交接记录测试：auto 直接结案、claude/human 进 open 队列、回写、园丁改进笔记。"""

from __future__ import annotations

from company import handoff

NOW = "2026-07-03T13:00:00"


def _store(tmp_path):
    return handoff.Store(str(tmp_path / "h.db"))


def test_auto_case_is_resolved_immediately(tmp_path):
    s = _store(tmp_path)
    cid = handoff.record(s, "fb", "你好想去北京", {"route": "auto", "reply_draft": "您好…"}, NOW)
    assert handoff.open_cases(s) == []  # auto 不进待办
    row = s.conn.execute("SELECT status,resolved_by,resolution FROM cases WHERE id=?", (cid,)).fetchone()
    assert row["status"] == "resolved" and row["resolved_by"] == "deepseek" and row["resolution"] == "您好…"


def test_escalated_cases_go_to_open_queue(tmp_path):
    s = _store(tmp_path)
    handoff.record(s, "fb", "要折扣", {"route": "human", "escalate_reason": "碰钱"}, NOW)
    handoff.record(s, "fb", "轮椅定制", {"route": "claude", "escalate_reason": "要判断"}, NOW)
    assert len(handoff.open_cases(s)) == 2
    assert len(handoff.open_cases(s, route="human")) == 1
    assert handoff.open_cases(s, route="claude")[0]["message"] == "轮椅定制"


def test_handoff_packet_has_what_claude_needs(tmp_path):
    s = _store(tmp_path)
    handoff.record(s, "fb", "要折扣", {
        "route": "human", "escalate_reason": "碰钱",
        "understood": "客户要折扣", "reply_draft": "我转接",
        "extracted": {"pax_count": 2, "cities": []},
    }, NOW)
    pkt = handoff.handoff_packet(handoff.open_cases(s)[0])
    assert pkt["客户消息"] == "要折扣" and pkt["为什么升级"] == "碰钱"
    assert pkt["已抽到的需求"] == {"pax_count": 2}  # 空 cities 被滤掉
    assert "守钱" in pkt["你的职责"]


def test_resolve_and_improvement(tmp_path):
    s = _store(tmp_path)
    cid = handoff.record(s, "fb", "要折扣", {"route": "human"}, NOW)
    handoff.resolve(s, cid, "已整理给老板拍板；发了稳住话术", "claude", NOW)
    assert handoff.open_cases(s) == []
    handoff.note_improvement(s, cid, "议价类", "inbox 提示词区分'谈价格'(claude可)与'定折扣'(human)", NOW)
    imp = s.conn.execute("SELECT pattern,suggest FROM improvements WHERE case_id=?", (cid,)).fetchone()
    assert imp["pattern"] == "议价类"
