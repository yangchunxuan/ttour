"""company/autopilot.py —— 让公司「自己往前走」的主循环（自主运行层）。

你要的是「harness 自主运行 agent 公司」。前面各岗位 agent、护栏、外部适配器都建好了，
但它们是**分开被调用**的。autopilot 把它们拧成一个**你一启动就持续自转的进程**：

  1) 收客户：SaleSmartly webhook 常驻（新消息 → 客服抽需求 → Lead 落库）
  2) 自动对账：定期查支付方(PayPal) PAID 状态，自动推进已付款订单（去掉人工逐笔确认）
  3) 盯闸门：把卡在人审闸门的事项（待发报价 / 待向供应商下单 / 待确认款）汇总成待办
  4) 反馈：定期产出转化漏斗 + 投放优化建议

🔴 安全边界（§3）不变：autopilot 只自动做**安全的自主动作**（收客户 / 读支付方权威
状态对账 / 统计）。**对外承诺——发报价给客户、向供应商正式下单——永远留人审**，
autopilot 只把它们排进「待办队列」提醒人，绝不自动执行。碰钱只认支付方权威 PAID，
不自己认账。

跑：
  python3 -m company.autopilot --interval 60          # 常驻自转（需 webhook/凭证环境变量）
  python3 -m company.autopilot --once                 # 只跑一轮对账+汇总（cron/验证用）
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from typing import Optional

from company.record.db import Database
from company.roles.collections import CollectionAgent
from company.roles.analytics import AnalyticsAgent
from company.console import op_pending, _payment_adapter


def tick(db: Database, payment_adapter=None) -> dict:
    """自转一轮（纯函数，可测）：自动对账 + 汇总待人审队列 + 转化快照。

    只做自主安全动作；不发报价、不下单（那些进 pending 待人审）。
    """
    coll = CollectionAgent(db, payment_adapter)
    reconciled = coll.reconcile_all()                # 读支付方状态自动推进已付款订单
    pending = op_pending(db)                          # 卡在人审闸门的待办
    funnel = AnalyticsAgent(db).funnel_summary()
    pending_count = sum(len(v) for v in pending.values())
    return {
        "reconciled": reconciled.get("reconciled", []),
        "pending_gates": pending,
        "pending_count": pending_count,
        "funnel": funnel,
    }


def _log(msg: str) -> None:
    print(f"[autopilot] {msg}", flush=True)


def run(db: Database, extractor=None, secret: Optional[str] = None,
        interval: int = 60, once: bool = False, serve_webhook: bool = True,
        host: str = "127.0.0.1", port: int = 8899, verify: bool = True,
        payment_adapter=None, max_ticks: Optional[int] = None) -> None:
    """启动自主运行循环。once=True → 只跑一轮（不起 webhook 服务）。"""
    if once:
        out = tick(db, payment_adapter)
        _log("单轮：自动推进 %d 单，待人审 %d 项" %
             (len(out["reconciled"]), out["pending_count"]))
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    httpd = None
    if serve_webhook and extractor is not None:
        from company.systems.salesmartly import build_server
        httpd, _ = build_server(db, extractor, secret=secret, host=host,
                                port=port, verify=verify)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        _log(f"SaleSmartly webhook 常驻 http://{host}:{port}/salesmartly/webhook "
             f"（验签={'开' if verify else '关'}）")

    _log(f"自主运行中，每 {interval}s 自转一轮。Ctrl-C 停。")
    ticks = 0
    try:
        while True:
            out = tick(db, payment_adapter)
            if out["reconciled"]:
                _log(f"自动推进已付款订单：{[r['order_id'] for r in out['reconciled']]}")
            _log(f"待人审 {out['pending_count']} 项 | 成交 {out['funnel']['counts']['won']} "
                 f"| GMV {out['funnel']['gmv']/100:.0f} 元")
            ticks += 1
            if max_ticks is not None and ticks >= max_ticks:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        _log("收到停止信号")
    finally:
        if httpd is not None:
            httpd.shutdown()
        _log("已停")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="定制游公司 · 自主运行 autopilot")
    ap.add_argument("--interval", type=int, default=60, help="自转间隔秒")
    ap.add_argument("--once", action="store_true", help="只跑一轮对账+汇总后退出")
    ap.add_argument("--no-webhook", action="store_true", help="不起 SaleSmartly webhook 服务")
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--no-verify", action="store_true", help="webhook 跳过验签（仅本机验证）")
    args = ap.parse_args(argv)

    db = Database(check_same_thread=False)
    extractor = None
    if not args.no_webhook and not args.once:
        from company.systems.salesmartly import _extractor_from_env
        extractor = _extractor_from_env()
    run(db, extractor=extractor, secret=os.environ.get("SS_WEBHOOK_SECRET"),
        interval=args.interval, once=args.once, serve_webhook=not args.no_webhook,
        port=args.port, verify=not args.no_verify, payment_adapter=_payment_adapter())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
