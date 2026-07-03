"""哨兵测试：新消息喂进流水线 + 去重（同一条只处理一次）。离线，注入假 brain。"""

from __future__ import annotations

import json

from company import handoff
from company.sentinel import Sentinel, manual_reader, stub_reader

NOW = "2026-07-03T14:00:00"


def _brain(route_fields: dict):
    base = {"understood": "x", "extracted": {}, "reply_draft": "您好…",
            "confidence": "high", "escalate": False, "money_or_commitment": False}
    base.update(route_fields)
    return lambda system, user: json.dumps(base, ensure_ascii=False)


def test_processes_new_and_dedups(tmp_path):
    drop = tmp_path / "drop.jsonl"
    drop.write_text('{"channel":"manual","sender":"A","text":"想去北京","msg_id":"m1"}\n',
                    encoding="utf-8")
    store = handoff.Store(str(tmp_path / "h.db"))
    s = Sentinel(_brain({}), store, [manual_reader(str(drop))], str(tmp_path / "seen.json"))

    first = s.tick(NOW)
    assert len(first) == 1 and first[0]["route"] == "auto"
    assert store.conn.execute("SELECT COUNT(*) c FROM cases").fetchone()["c"] == 1

    # 再跑一轮：同一条消息不该被再处理（去重）
    assert s.tick(NOW) == []
    assert store.conn.execute("SELECT COUNT(*) c FROM cases").fetchone()["c"] == 1


def test_new_line_gets_picked_up(tmp_path):
    drop = tmp_path / "drop.jsonl"
    drop.write_text('{"channel":"facebook","sender":"A","text":"你好","msg_id":"a"}\n', encoding="utf-8")
    store = handoff.Store(str(tmp_path / "h.db"))
    s = Sentinel(_brain({}), store, [manual_reader(str(drop))], str(tmp_path / "seen.json"))
    s.tick(NOW)
    # 追加一条新消息 → 下一轮才处理它、不重复旧的
    with open(drop, "a", encoding="utf-8") as f:
        f.write('{"channel":"facebook","sender":"B","text":"要打折","msg_id":"b"}\n')
    second = s.tick(NOW)
    assert len(second) == 1 and second[0]["sender"] == "B"
    assert store.conn.execute("SELECT COUNT(*) c FROM cases").fetchone()["c"] == 2


def test_seen_persists_across_restart(tmp_path):
    drop = tmp_path / "drop.jsonl"
    drop.write_text('{"channel":"manual","sender":"A","text":"hi","msg_id":"m1"}\n', encoding="utf-8")
    seen = str(tmp_path / "seen.json")
    store = handoff.Store(str(tmp_path / "h.db"))
    Sentinel(_brain({}), store, [manual_reader(str(drop))], seen).tick(NOW)
    # 新起一个哨兵（模拟重启）：已见过的不该重复处理
    s2 = Sentinel(_brain({}), store, [manual_reader(str(drop))], seen)
    assert s2.tick(NOW) == []


def test_reader_error_does_not_crash_tick(tmp_path):
    store = handoff.Store(str(tmp_path / "h.db"))

    def boom():
        raise RuntimeError("读取器炸了")

    s = Sentinel(_brain({}), store, [boom], str(tmp_path / "seen.json"))
    out = s.tick(NOW)  # 不该抛，坏读取器记一条错误即可
    assert any("reader_error" in x for x in out)


def test_stub_reader_returns_empty(tmp_path):
    assert stub_reader("facebook")() == []
