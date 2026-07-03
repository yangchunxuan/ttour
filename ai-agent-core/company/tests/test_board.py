"""运营台看板测试：case 序列化给前端 + 服务端点。"""

from __future__ import annotations

import json

from company import handoff
from company import board

NOW = "2026-07-03T13:30:00"


def _store(tmp_path):
    s = handoff.Store(str(tmp_path / "h.db"))
    handoff.record(s, "facebook", "想去北京", {"route": "auto", "reply_draft": "您好…"}, NOW)
    handoff.record(s, "instagram", "要折扣", {"route": "human", "escalate_reason": "碰钱",
                                             "understood": "客户要折扣"}, NOW)
    return s


def test_cases_json_deserializes_decision(tmp_path):
    s = _store(tmp_path)
    data = board._cases_json(s)
    assert len(data) == 2
    # decision 反序列化成 dict 给前端
    human = [c for c in data if c["route"] == "human"][0]
    assert isinstance(human["decision"], dict) and human["decision"]["understood"] == "客户要折扣"


def test_cases_json_open_first(tmp_path):
    s = _store(tmp_path)
    data = board._cases_json(s)
    # open（human）应排在 resolved（auto）前面
    assert data[0]["status"] == "open"


def test_page_has_three_columns():
    assert "%COLS%" in board._PAGE  # 模板占位
    routes = [c[0] for c in board._COLUMNS]
    assert routes == ["human", "claude", "auto"]


def test_server_serves_page_and_api(tmp_path):
    import threading, urllib.request
    s = handoff.Store(str(tmp_path / "srv.db"), check_same_thread=False)  # 服务在别的线程
    handoff.record(s, "facebook", "想去北京", {"route": "auto"}, NOW)
    handoff.record(s, "instagram", "要折扣", {"route": "human"}, NOW)
    httpd = board.build_server(s, port=8091)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        page = urllib.request.urlopen("http://127.0.0.1:8091/", timeout=5).read().decode()
        assert "运营台" in page
        cases = json.loads(urllib.request.urlopen("http://127.0.0.1:8091/api/cases", timeout=5).read())
        assert len(cases) == 2
    finally:
        httpd.shutdown()
