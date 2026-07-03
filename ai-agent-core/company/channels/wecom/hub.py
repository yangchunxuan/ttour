"""company/channels/wecom/hub.py — 把企业微信回调事件接到 company 流水线的"总线"。

一条链:
  回调事件(有新客服消息) → sync_msg 增量拉取 → 每条**客户**文本消息
    → inbox.decide(DeepSeek 判) → handoff.record(上看板/留痕)
    → route==auto 且开了自动回 → send_text 回客户;否则等人/Claude 在看板处理。

细节:
  * cursor 按 open_kfid 持久化到文件,重启从上次续拉(不重复、不漏)。
  * msgid 去重(sync_msg 可能重复返回)。
  * 只处理 origin==3(客户发来的)且 msgtype=='text' 的;其它先跳过(第一版聚焦文本)。
  * 把回信目标(external_userid/open_kfid/msgid)存进 case 的 decision 里,方便看板批准后回信。
  * 回调线程只负责入队,拉取/判定在后台 worker,避免卡住企业微信 5s 响应窗口。
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from datetime import datetime, timezone
from pathlib import Path

from company import handoff
from company.inbox import Brain, decide
from company.channels.wecom.client import WecomError

log = logging.getLogger("wecom.hub")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CursorStore:
    """按 open_kfid 存 sync_msg 的 next_cursor。落地到一个小 JSON 文件。"""

    def __init__(self, path: str):
        self.path = Path(path)
        self.data: dict[str, str] = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                self.data = {}

    def get(self, kfid: str) -> str:
        return self.data.get(kfid, "")

    def set(self, kfid: str, cursor: str) -> None:
        self.data[kfid] = cursor
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")


class KfHub:
    def __init__(self, client, brain: Brain, store: "handoff.Store",
                 cursor_path: str, *, auto_reply: bool = False,
                 max_pages: int = 20):
        self.client = client
        self.brain = brain
        self.store = store
        self.cursors = CursorStore(cursor_path)
        self.auto_reply = auto_reply
        self.max_pages = max_pages
        self.seen: set[str] = set()
        self._q: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self._worker: threading.Thread | None = None

    # ---- 回调线程:只入队 ---- #
    def handle_event(self, event: dict) -> None:
        if event.get("Event") == "kf_msg_or_event" or event.get("MsgType") == "event":
            self._q.put((event.get("OpenKfId") or "", event.get("Token") or ""))

    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def _run(self) -> None:
        while True:
            kfid, token = self._q.get()
            try:
                self.pull(kfid, token)
            except Exception:  # noqa: BLE001
                log.exception("拉取处理异常 kfid=%s", kfid)

    # ---- 拉取 + 分发(可单测直接调) ---- #
    def pull(self, kfid: str, token: str = "") -> int:
        """把某客服账号的新消息拉完并分发。返回本次处理的客户消息条数。"""
        cursor = self.cursors.get(kfid)
        handled = 0
        for _ in range(self.max_pages):
            resp = self.client.sync_msg(cursor=cursor, token=token, open_kfid=kfid)
            for m in resp.get("msg_list", []) or []:
                if self._dispatch(m):
                    handled += 1
            cursor = resp.get("next_cursor", cursor) or cursor
            self.cursors.set(kfid, cursor)
            if not resp.get("has_more"):
                break
        return handled

    def _dispatch(self, m: dict) -> bool:
        msgid = m.get("msgid", "")
        if msgid and msgid in self.seen:
            return False
        if msgid:
            self.seen.add(msgid)
        if m.get("msgtype") != "text":
            return False              # 第一版只处理文本
        if m.get("origin") != 3:
            return False              # 只处理客户发来的(3=客户,4/5=系统/接待人员)
        text = (m.get("text") or {}).get("content", "")
        if not text:
            return False

        d = decide(self.brain, text)
        dd = d.to_dict()
        dd["_reply"] = {                # 存回信目标,方便看板批准后回信
            "touser": m.get("external_userid", ""),
            "open_kfid": m.get("open_kfid", ""),
            "msgid": msgid,
        }
        handoff.record(self.store, "wecom_kf", text, dd, _now())

        if d.route == "auto" and self.auto_reply and d.reply_draft:
            try:
                self.client.send_text(
                    touser=m.get("external_userid", ""),
                    open_kfid=m.get("open_kfid", ""),
                    content=d.reply_draft)
            except WecomError:
                log.exception("自动回复发送失败(已留痕在看板)")
        return True
