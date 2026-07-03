"""company/handoff.py — 升级/交接记录 + 给 Claude 的交接包（把三层真正接起来）。

DeepSeek(一号)判定要升级(claude/human)时，往这里落一条 case；Claude(二号)/人
盯着队列拿起来处理、回写。这就是"DeepSeek 怎么唤醒 Claude"的落地：**来活了 = 队列里
多一条 open case**。刻意做薄——自带一个小 sqlite，不耦合重架构。

园丁闭环：Claude 处理时可以顺手记一条 improvement（"DeepSeek 这类老栽，harness 该这么
改"），攒起来就是把 DeepSeek 越养越能干的清单。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_DDL = """
CREATE TABLE IF NOT EXISTS cases(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT, message TEXT,
  route TEXT,              -- auto | claude | human
  decision TEXT,           -- DeepSeek 的判断(JSON)
  status TEXT DEFAULT 'open',   -- open | resolved
  resolution TEXT,         -- 处理结果(回客户的话/给人的说明)
  resolved_by TEXT,        -- deepseek | claude | human
  created_at TEXT, resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS improvements(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  case_id INTEGER, pattern TEXT, suggest TEXT, created_at TEXT
);
"""


@dataclass
class Store:
    path: str

    def __post_init__(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_DDL)
        self.conn.commit()

    def close(self):
        self.conn.close()


def record(store: Store, source: str, message: str, decision: dict, now: str) -> int:
    """落一条 case。route=auto 也记（留痕/供陪审）；claude/human 则是 open 待办。"""
    route = decision.get("route", "auto")
    cur = store.conn.execute(
        "INSERT INTO cases(source,message,route,decision,status,created_at) VALUES(?,?,?,?,?,?)",
        (source, message, route, json.dumps(decision, ensure_ascii=False),
         "resolved" if route == "auto" else "open", now),
    )
    if route == "auto":  # DeepSeek 自理的直接标已处理，回复就是它的 draft
        store.conn.execute(
            "UPDATE cases SET resolution=?, resolved_by='deepseek', resolved_at=? WHERE id=?",
            (decision.get("reply_draft", ""), now, cur.lastrowid))
    store.conn.commit()
    return cur.lastrowid


def open_cases(store: Store, route: Optional[str] = None) -> list[dict]:
    """待办队列（Claude/人 从这里拿活）。route 可筛 'claude' / 'human'。"""
    sql = "SELECT * FROM cases WHERE status='open'"
    args: tuple = ()
    if route:
        sql += " AND route=?"
        args = (route,)
    sql += " ORDER BY id"
    return [dict(r) for r in store.conn.execute(sql, args).fetchall()]


def handoff_packet(case: dict) -> dict:
    """给二号员工(Claude)的交接包：它接手需要的一切。"""
    dec = json.loads(case["decision"]) if isinstance(case.get("decision"), str) else (case.get("decision") or {})
    return {
        "case_id": case["id"],
        "为什么升级": dec.get("escalate_reason") or ("碰钱/承诺" if case["route"] == "human" else ""),
        "客户消息": case["message"],
        "DeepSeek 的理解": dec.get("understood", ""),
        "DeepSeek 拟的回复": dec.get("reply_draft", ""),
        "已抽到的需求": {k: v for k, v in (dec.get("extracted") or {}).items() if v not in (None, [], "")},
        "你的职责": ("守钱和承诺：碰钱/对客户承诺的，整理清楚交给人拍板，别自己应承。"
                  if case["route"] == "human" else
                  "二号员工：按定制游工作流处理这条，给出更稳妥的回复；碰钱/承诺才升级给人。"),
    }


def resolve(store: Store, case_id: int, resolution: str, by: str, now: str) -> None:
    store.conn.execute(
        "UPDATE cases SET status='resolved', resolution=?, resolved_by=?, resolved_at=? WHERE id=?",
        (resolution, by, now, case_id))
    store.conn.commit()


def note_improvement(store: Store, case_id: int, pattern: str, suggest: str, now: str) -> int:
    """园丁记一笔：DeepSeek 这类老栽，harness 该怎么改（攒着做 harness 完善）。"""
    cur = store.conn.execute(
        "INSERT INTO improvements(case_id,pattern,suggest,created_at) VALUES(?,?,?,?)",
        (case_id, pattern, suggest, now))
    store.conn.commit()
    return cur.lastrowid
