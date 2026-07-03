"""运营控制台 —— 把公司变成一个你能真正「启动 + 日常操作」的运行系统。

模型：leads/报价/订单 的状态沉在 company.record 库里；agent 自动把能推的推到
下一个人审闸门；**人（你）在控制台看待办、在闸门处点批准，公司就往前走一步**。
这就是「运行中的公司」该有的样子——全自动 + 人守钱和承诺（§3）。

跑：
  python3 -m company.console intake --fields '{"pax_count":2,...}'   # 进一条线索
  python3 -m company.console pending                                  # 看所有待办(卡在哪个闸门)
  python3 -m company.console send 1        # 闸门1：批准发报价 → 建订单
  python3 -m company.console deposit 1     # 闸门2：确认定金到账
  python3 -m company.console book 1        # 闸门3：批准向供应商下单
  python3 -m company.console balance 1     # 闸门4：确认尾款 → 结清
  python3 -m company.console funnel        # 转化统计 + 投放建议

真实里 intake 的对话可走 LLM(设 BROKER_* 环境变量)或 SaleSmartly 适配器；
这里核心是「状态机 + 待办 + 人审闸门」的运营面，可离线操作。
"""

from __future__ import annotations

import argparse
import json
import os

from company.record.db import Database
from company.record.models import Order
from company.record import costing
from company.roles.customer_service import CustomerServiceAgent, make_llm_extractor
from company.roles.operations import OperationsAgent, get_markup
from company.roles.collections import CollectionAgent
from company.roles.procurement import ProcurementAgent
from company.roles.analytics import AnalyticsAgent
from company.pipeline import auto_plan


def _payment_adapter():
    """有 PayPal 凭证 → 真 PayPal 收款（可自动对账）；否则占位（人工确认）。"""
    if os.environ.get("PAYPAL_CLIENT_ID"):
        from company.systems.paypal import from_env
        return from_env()
    return None  # CollectionAgent 默认用 StubPaymentAdapter


# ---- 每个操作 = 一个可测函数（CLI 只是薄壳）---- #

def op_intake(db: Database, fields: dict, extractor=None) -> dict:
    """进一条线索：客服抽需求 → 若齐则计调自动出报价（停在 pending_review 待发）。"""
    ext = extractor or (lambda c, s: dict(fields))
    cs = CustomerServiceAgent(db, ext)
    an = AnalyticsAgent(db)
    r = cs.ingest(json.dumps(fields, ensure_ascii=False), source="console")
    an.record_event("inquiry", lead_id=r["lead_id"])
    if r["status"] != "qualified":
        return {"lead_id": r["lead_id"], "stage": "collecting",
                "missing": r["missing"], "follow_up": r["follow_up"]}
    an.record_event("valid", lead_id=r["lead_id"])
    lead = db.get_lead(r["lead_id"])
    q = OperationsAgent(db).build_quote(r["lead_id"], auto_plan(db, lead))
    if q["status"] != "quoted":
        return {"lead_id": r["lead_id"], "stage": "need_pricebook", "missing": q.get("missing")}
    an.record_event("quoted", lead_id=r["lead_id"])
    return {"lead_id": r["lead_id"], "stage": "quote_pending_review",
            "quote_id": q["quote_id"], "price_yuan": q["price"] / 100,
            "next_gate": f"console send {q['quote_id']}"}


def op_pending(db: Database) -> dict:
    """所有卡在人审闸门的待办。"""
    quotes = db.conn.execute(
        "SELECT id, lead_id, quote_price FROM quotes WHERE status='pending_review'").fetchall()
    dep = db.conn.execute(
        "SELECT id, deposit_amount FROM orders WHERE deposit_status='unpaid'").fetchall()
    bookings = db.conn.execute(
        "SELECT DISTINCT quote_id FROM bookings WHERE status='pending'").fetchall()
    bal = db.conn.execute(
        "SELECT id, balance_amount FROM orders WHERE deposit_status='paid' "
        "AND balance_status='unpaid'").fetchall()
    return {
        "待发报价(闸门1)": [{"quote": r["id"], "对客价元": r["quote_price"] / 100} for r in quotes],
        "待确认定金(闸门2)": [{"order": r["id"], "定金元": r["deposit_amount"] / 100} for r in dep],
        "待向供应商下单(闸门3)": [r["quote_id"] for r in bookings],
        "待确认尾款(闸门4)": [{"order": r["id"], "尾款元": r["balance_amount"] / 100} for r in bal],
    }


def op_send(db: Database, quote_id: int, needs_transport: bool = True) -> dict:
    """闸门1：批准发报价 → 标 sent + 建订单（定金规则）。"""
    q = db.conn.execute("SELECT * FROM quotes WHERE id=?", (quote_id,)).fetchone()
    if not q:
        return {"error": f"报价 {quote_id} 不存在"}
    db.conn.execute("UPDATE quotes SET status='sent' WHERE id=?", (quote_id,))
    db.conn.commit()
    pct = costing.pick_deposit_pct(needs_transport)
    dep = costing.deposit_amount(q["quote_price"], pct)
    oid = db.add_order(Order(quote_id=quote_id, needs_advance_transport=needs_transport,
                             deposit_pct=pct, deposit_amount=dep,
                             balance_amount=q["quote_price"] - dep))
    req = CollectionAgent(db, _payment_adapter()).request_deposit(oid)
    from company.roles import disclosures
    import json as _json
    commitments = _json.loads(q["commitments"] or "{}")
    return {"quote_id": quote_id, "order_id": oid,
            "对客报价话术": disclosures.quote_message(q["quote_price"] / 100, commitments),
            "定金收款单": req["instruction"],
            "next_gate": f"console deposit {oid}"}


def op_deposit(db: Database, order_id: int) -> dict:
    """闸门2：确认定金到账。"""
    CollectionAgent(db).confirm_deposit_paid(order_id)
    o = db.conn.execute("SELECT quote_id FROM orders WHERE id=?", (order_id,)).fetchone()
    return {"order_id": order_id, "stage": "deposit_paid",
            "next_gate": f"console book {order_id}"}


def op_book(db: Database, order_id: int) -> dict:
    """闸门3：批准向供应商下单。"""
    o = db.conn.execute("SELECT quote_id FROM orders WHERE id=?", (order_id,)).fetchone()
    proc = ProcurementAgent(db)
    ids = proc.plan_bookings(o["quote_id"], order_id)
    for bid in ids:
        proc.confirm_booking(bid)
    return {"order_id": order_id, "供应商下单数": len(ids),
            "next_gate": f"console balance {order_id}"}


def op_balance(db: Database, order_id: int) -> dict:
    """闸门4：确认尾款到账 → 结清 + 记成交。"""
    coll = CollectionAgent(db)
    coll.request_balance(order_id)
    coll.confirm_balance_paid(order_id)
    o = db.conn.execute("SELECT quote_id FROM orders WHERE id=?", (order_id,)).fetchone()
    q = db.conn.execute("SELECT lead_id, quote_price FROM quotes WHERE id=?",
                        (o["quote_id"],)).fetchone()
    AnalyticsAgent(db).record_event("won", lead_id=q["lead_id"], amount=q["quote_price"])
    return {"order_id": order_id, "stage": "settled", "成交元": q["quote_price"] / 100}


def op_funnel(db: Database) -> dict:
    an = AnalyticsAgent(db)
    return {"漏斗": an.funnel_summary(), "投放建议": an.optimization_hints("platform")}


def op_market(db: Database) -> dict:
    """V2 一·3 反馈前端：整体漏斗 + 各维度全阶段拆解 + 可执行预算建议。"""
    from company.roles.marketing import MarketingAgent
    return MarketingAgent(db).feedback_report()


def op_reconcile(db: Database) -> dict:
    """自动对账：查支付方(PayPal)权威状态，把已 PAID 的定金/尾款自动推进。
    无 PayPal 凭证时保持人工确认（deposit/balance 命令）。"""
    return CollectionAgent(db, _payment_adapter()).reconcile_all()


def op_requote(db: Database, parent_quote_id: int, markup: float | None = None) -> dict:
    """二次报价（V2 低价引流→按实际需求重报）：生成父单下一版本，仍待人审后发。"""
    r = OperationsAgent(db).re_quote(parent_quote_id, markup=markup)
    if r["status"] != "quoted":
        return r
    return {"status": "requoted", "quote_id": r["quote_id"], "version": r["version"],
            "parent_quote_id": r["parent_quote_id"], "对客价元": r["price"] / 100,
            "较上版元": r["delta"] / 100, "next_gate": f"console send {r['quote_id']}"}


# ---- CLI 薄壳 ---- #
def main() -> int:
    ap = argparse.ArgumentParser(description="定制游公司运营控制台")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("intake"); pi.add_argument("--fields", required=True)
    pi.add_argument("--broker", action="store_true", help="用 LLM 从对话抽取(需 BROKER_* 环境变量)")
    sub.add_parser("pending")
    for name in ("send", "deposit", "book", "balance"):
        p = sub.add_parser(name); p.add_argument("id", type=int)
    sub.add_parser("funnel")
    sub.add_parser("market")
    sub.add_parser("reconcile")
    prq = sub.add_parser("requote"); prq.add_argument("parent_quote_id", type=int)
    prq.add_argument("--markup", type=float, default=None)
    args = ap.parse_args()

    db = Database()
    if args.cmd == "intake":
        fields = json.loads(args.fields)
        extractor = None
        if args.broker:
            extractor = make_llm_extractor(os.environ["BROKER_BASE_URL"],
                                           os.environ["BROKER_TOKEN"])
        out = op_intake(db, fields, extractor)
    elif args.cmd == "pending":
        out = op_pending(db)
    elif args.cmd == "send":
        out = op_send(db, args.id)
    elif args.cmd == "deposit":
        out = op_deposit(db, args.id)
    elif args.cmd == "book":
        out = op_book(db, args.id)
    elif args.cmd == "balance":
        out = op_balance(db, args.id)
    elif args.cmd == "funnel":
        out = op_funnel(db)
    elif args.cmd == "market":
        out = op_market(db)
    elif args.cmd == "reconcile":
        out = op_reconcile(db)
    elif args.cmd == "requote":
        out = op_requote(db, args.parent_quote_id, args.markup)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
