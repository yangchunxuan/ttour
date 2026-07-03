"""company/sentinel.py — 哨兵:后台常驻，只读地盯着消息软件，发现新消息就喂进流水线。

你的场景（没有 API，靠"盯着真实软件看"）的正解。分工刻意分明、省钱：
  * 哨兵（本文件，**免费**）：每隔几秒让各"读取器"看一眼有没有**新**消息，去重。
  * 有真新消息 → 才喂给 **DeepSeek**（inbox 分流，花钱在刀刃上）→ 落 handoff → 上看板。
  * 拿不准升 Claude、碰钱找人（看板三栏）。

读取器是**可插拔**的（channel_reader() -> list[InboundMsg]）：
  * ManualReader：从一个投递文件读（今天就能跑通整条链，也是最稳的兜底）。
  * facebook / wechat：之后接真实软件（浏览器读 FB / 截图读微信）——先留接口。

安全：哨兵**只读**、只登记；回复一律先上看板等人/Claude 审，绝不自动发出去。
"""

from __future__ import annotations

import json
import time as _time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from company.inbox import decide, Brain
from company import handoff

# 读取器：调一次返回"当前能看到的消息"（可重复返回，去重交给哨兵）
ChannelReader = Callable[[], list["InboundMsg"]]


@dataclass
class InboundMsg:
    channel: str      # facebook | wechat | manual …
    sender: str       # 客户标识（名字/ID）
    text: str
    msg_id: str       # 去重键：同一条只处理一次


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Sentinel:
    """常驻哨兵：轮询各读取器 → 去重 → 新消息喂进 inbox → 落 handoff（上看板）。"""

    def __init__(self, brain: Brain, store: "handoff.Store",
                 readers: list[ChannelReader], seen_path: str):
        self.brain = brain
        self.store = store
        self.readers = readers
        self.seen_path = Path(seen_path)
        self.seen: set[str] = set()
        if self.seen_path.exists():
            try:
                self.seen = set(json.loads(self.seen_path.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001
                self.seen = set()

    def _save_seen(self) -> None:
        self.seen_path.parent.mkdir(parents=True, exist_ok=True)
        self.seen_path.write_text(json.dumps(sorted(self.seen), ensure_ascii=False), encoding="utf-8")

    def tick(self, now: str | None = None) -> list[dict]:
        """跑一轮：读所有读取器，只处理**没见过**的消息。返回本轮新处理的 case 概要。"""
        now = now or _now()
        handled: list[dict] = []
        for reader in self.readers:
            try:
                msgs = reader() or []
            except Exception as e:  # noqa: BLE001 - 单个读取器坏了别拖垮整轮
                handled.append({"reader_error": str(e)})
                continue
            for m in msgs:
                key = f"{m.channel}:{m.msg_id}"
                if key in self.seen:
                    continue
                self.seen.add(key)
                d = decide(self.brain, m.text)
                cid = handoff.record(self.store, m.channel, m.text, d.to_dict(), now)
                handled.append({"case_id": cid, "channel": m.channel,
                                "sender": m.sender, "route": d.route})
        self._save_seen()
        return handled

    def run(self, interval: float = 5.0, on_tick: Callable[[list[dict]], None] | None = None) -> None:
        """常驻循环。Ctrl-C 停。interval=多少秒看一眼。"""
        print(f"[哨兵] 开工，每 {interval}s 看一眼；读取器：{len(self.readers)} 个")
        try:
            while True:
                new = self.tick()
                if new and on_tick:
                    on_tick(new)
                _time.sleep(interval)
        except KeyboardInterrupt:
            print("\n[哨兵] 停")


# ---- 读取器 ---- #
def manual_reader(drop_path: str) -> ChannelReader:
    """从投递文件读（每行一条 JSON：{channel,sender,text,msg_id?}）。今天就能跑通整条链。
    你往这个文件里 append 一行，就等于"来了一条新消息"。"""
    def read() -> list[InboundMsg]:
        p = Path(drop_path)
        if not p.exists():
            return []
        out: list[InboundMsg] = []
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            out.append(InboundMsg(
                channel=str(d.get("channel", "manual")),
                sender=str(d.get("sender", "客户")),
                text=str(d.get("text", "")),
                msg_id=str(d.get("msg_id", f"{drop_path}:{i}")),  # 无 id 用行号，去重靠它
            ))
        return out
    return read


def stub_reader(channel: str) -> ChannelReader:
    """facebook / wechat 的占位读取器：真实读取（浏览器读 FB / 截图读微信）之后接上。
    现在恒返回空——留住接口、不假装有消息。"""
    def read() -> list[InboundMsg]:
        return []
    return read
