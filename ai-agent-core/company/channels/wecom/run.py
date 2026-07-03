"""company/channels/wecom/run.py — 启动企业微信·微信客服接入(回调服务 + 总线)。

一条命令把零件拼起来跑:
  回调服务(收企业微信推送) → 总线(拉消息→DeepSeek判→上看板/自动回)。

用法(在项目 venv 里):
  .venv/bin/python -m company.channels.wecom.run --port 9000
  .venv/bin/python -m company.channels.wecom.run --port 9000 --auto-reply   # 开自动回

读 ~/.wecom/creds.env:CorpID / Secret / CallbackToken / CallbackAESKey(和后台填的一致)。
DeepSeek 可选:设了环境变量 DEEPSEEK_API_KEY 就用它判;没设则用**保守 stub**(一律上看板、
不自动回),先把管道跑通,之后再接真 DeepSeek。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from company import handoff
from company.channels.wecom.callback import make_server
from company.channels.wecom.client import from_config
from company.channels.wecom.config import load_config
from company.channels.wecom.crypto import WXBizMsgCrypt
from company.channels.wecom.hub import KfHub


def _make_brain():
    """有 DeepSeek key 就用它;没有则用保守 stub(全部升级给人,不自动回)。"""
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
    if key:
        from company.inbox import make_broker_brain
        return make_broker_brain(base, key, model), f"DeepSeek({model})"

    def stub(system: str, user: str) -> str:
        return json.dumps({
            "understood": "(未接 DeepSeek)", "extracted": {}, "reply_draft": "",
            "confidence": "low", "escalate": True,
            "escalate_reason": "未配置 DeepSeek,先走人工", "money_or_commitment": False,
        }, ensure_ascii=False)

    return stub, "stub(未接 DeepSeek → 全部上看板)"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="企业微信·微信客服接入")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--auto-reply", action="store_true",
                    help="auto 路由的消息自动回客户(默认关:先上看板等人审)")
    ap.add_argument("--db", default=os.path.expanduser("~/.wecom/handoff.db"))
    ap.add_argument("--cursor", default=os.path.expanduser("~/.wecom/cursor.json"))
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    cfg = load_config()
    if not cfg.has_callback:
        print("❌ 缺 WECOM_CALLBACK_TOKEN / WECOM_CALLBACK_AESKEY。")
        print("   先生成一对并写进 ~/.wecom/creds.env,且和企业微信后台『接收消息服务器』里填的一致。")
        return 1

    client = from_config(cfg)
    crypt = WXBizMsgCrypt(cfg.callback_token, cfg.callback_aeskey, cfg.corpid)
    store = handoff.Store(args.db, check_same_thread=False)
    brain, brain_name = _make_brain()
    hub = KfHub(client, brain, store, args.cursor, auto_reply=args.auto_reply)
    hub.start()
    srv = make_server(crypt, hub.handle_event, host=args.host, port=args.port)

    print("✅ 企业微信·微信客服接入已启动")
    print(f"   大脑    : {brain_name}")
    print(f"   自动回  : {'开' if args.auto_reply else '关(先上看板等人审)'}")
    print(f"   回调服务: http://{args.host}:{args.port}   ← 穿透把公网 HTTPS 转发到这")
    print(f"   看板库  : {args.db}")
    print("   (Ctrl-C 停)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
