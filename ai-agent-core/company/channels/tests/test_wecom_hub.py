"""总线测试:回调事件→拉消息→判→上看板/自动回。离线,注入假 client + 假 brain。"""

from __future__ import annotations

import json

from company import handoff
from company.channels.wecom.hub import KfHub, CursorStore


class FakeClient:
    """假企业微信客户端:sync_msg 按预置分页返回;send_text 记录发了啥。"""
    def __init__(self, pages):
        self.pages = list(pages)
        self.sent = []
        self.sync_calls = []

    def sync_msg(self, *, cursor="", token="", open_kfid="", limit=1000):
        self.sync_calls.append({"cursor": cursor, "token": token, "kfid": open_kfid})
        return self.pages.pop(0) if self.pages else {"msg_list": [], "next_cursor": cursor, "has_more": 0}

    def send_text(self, *, touser, open_kfid, content, msgid=None):
        self.sent.append({"touser": touser, "open_kfid": open_kfid, "content": content})
        return {"errcode": 0, "msgid": "sent1"}


def _brain(**fields):
    base = {"understood": "x", "extracted": {}, "reply_draft": "您好,已收到~",
            "confidence": "high", "escalate": False, "money_or_commitment": False}
    base.update(fields)
    return lambda system, user: json.dumps(base, ensure_ascii=False)


def _cust_text(msgid, content="想订张家界三日游", kfid="wkKF01", ext="extUSER"):
    return {"msgid": msgid, "msgtype": "text", "origin": 3, "open_kfid": kfid,
            "external_userid": ext, "text": {"content": content}}


def _page(msgs, next_cursor="c_next", has_more=0):
    return {"msg_list": msgs, "next_cursor": next_cursor, "has_more": has_more}


def test_customer_text_records_and_auto_replies(tmp_path):
    client = FakeClient([_page([_cust_text("m1")])])
    store = handoff.Store(str(tmp_path / "h.db"))
    hub = KfHub(client, _brain(), store, str(tmp_path / "cur.json"), auto_reply=True)
    n = hub.pull("wkKF01", token="tk")
    assert n == 1
    rows = store.conn.execute("SELECT source,route,resolution FROM cases").fetchall()
    assert rows[0]["source"] == "wecom_kf" and rows[0]["route"] == "auto"
    # auto + 开了自动回 → 真发了一条,内容=DeepSeek 的 draft,发给正确客户
    assert client.sent == [{"touser": "extUSER", "open_kfid": "wkKF01", "content": "您好,已收到~"}]


def test_auto_reply_off_records_but_does_not_send(tmp_path):
    client = FakeClient([_page([_cust_text("m1")])])
    store = handoff.Store(str(tmp_path / "h.db"))
    hub = KfHub(client, _brain(), store, str(tmp_path / "cur.json"), auto_reply=False)
    hub.pull("wkKF01")
    assert store.conn.execute("SELECT COUNT(*) c FROM cases").fetchone()["c"] == 1
    assert client.sent == []          # 默认不自动发,只上看板等人审


def test_money_routes_human_and_never_autosends(tmp_path):
    client = FakeClient([_page([_cust_text("m1", "这个行程能便宜到5000吗?")])])
    store = handoff.Store(str(tmp_path / "h.db"))
    hub = KfHub(client, _brain(money_or_commitment=True), store,
                str(tmp_path / "cur.json"), auto_reply=True)   # 即便开了自动回
    hub.pull("wkKF01")
    row = store.conn.execute("SELECT route,status FROM cases").fetchone()
    assert row["route"] == "human" and row["status"] == "open"
    assert client.sent == []          # 碰钱是硬闸门,绝不自动发


def test_dedup_same_msgid(tmp_path):
    client = FakeClient([_page([_cust_text("m1"), _cust_text("m1")])])   # 同一条重复
    store = handoff.Store(str(tmp_path / "h.db"))
    hub = KfHub(client, _brain(), store, str(tmp_path / "cur.json"))
    hub.pull("wkKF01")
    assert store.conn.execute("SELECT COUNT(*) c FROM cases").fetchone()["c"] == 1


def test_ignores_system_and_nontext(tmp_path):
    msgs = [
        {"msgid": "s1", "msgtype": "text", "origin": 4, "text": {"content": "系统消息"}},
        {"msgid": "s2", "msgtype": "image", "origin": 3, "image": {}},
        _cust_text("real"),
    ]
    client = FakeClient([_page(msgs)])
    store = handoff.Store(str(tmp_path / "h.db"))
    hub = KfHub(client, _brain(), store, str(tmp_path / "cur.json"))
    assert hub.pull("wkKF01") == 1     # 只有那条真正的客户文本被处理


def test_cursor_persisted_across_pages_and_restart(tmp_path):
    client = FakeClient([
        _page([_cust_text("m1")], next_cursor="c1", has_more=1),
        _page([_cust_text("m2")], next_cursor="c2", has_more=0),
    ])
    store = handoff.Store(str(tmp_path / "h.db"))
    cur_path = str(tmp_path / "cur.json")
    hub = KfHub(client, _brain(), store, cur_path)
    assert hub.pull("wkKF01") == 2
    # 分页:第二次 sync 用的 cursor 应是第一页返回的 c1
    assert client.sync_calls[1]["cursor"] == "c1"
    # 落盘的 cursor 是最终的 c2;新建 hub(模拟重启)能读到
    assert CursorStore(cur_path).get("wkKF01") == "c2"


def test_reply_target_saved_in_case(tmp_path):
    client = FakeClient([_page([_cust_text("m1")])])
    store = handoff.Store(str(tmp_path / "h.db"))
    hub = KfHub(client, _brain(), store, str(tmp_path / "cur.json"))
    hub.pull("wkKF01")
    dec = json.loads(store.conn.execute("SELECT decision FROM cases").fetchone()["decision"])
    assert dec["_reply"] == {"touser": "extUSER", "open_kfid": "wkKF01", "msgid": "m1"}
