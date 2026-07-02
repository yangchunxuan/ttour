"""公司流水线（总指挥）—— 把六大流程的岗位 agent 串成一条自主运行的线。

一条客户对话进来 → 自动流过 客服→计调→报价→收款→供应商下单→尾款→结清，
**只在碰钱/对客户或供应商承诺的"人审闸门"处停下等人点头**（§3 业务闸门）。
其余全自动、全程护栏。真实世界触点（读 SaleSmartly / 支付页 / 供应商系统）是
各 agent 里的可插拔适配器，这一层不关心。

approver(gate, context) -> bool：人审回调。真实里是人/UI；测试/演示里可自动放行。
每一步都记 funnel 事件，数据分析 agent 随时可统计。
"""

from __future__ import annotations

from typing import Callable

from company.record.db import Database
from company.record.models import Order
from company.record import costing
from company.roles.customer_service import CustomerServiceAgent
from company.roles.operations import OperationsAgent
from company.roles.collections import CollectionAgent
from company.roles.procurement import ProcurementAgent
from company.roles.analytics import AnalyticsAgent

# 人审闸门名（§3：碰钱/对客户或供应商承诺）
GATE_SEND_QUOTE = "send_quote"          # 把报价发给客户前
GATE_DEPOSIT = "deposit_received"        # 确认定金到账（碰钱）
GATE_BOOK_SUPPLIERS = "book_suppliers"   # 向供应商正式下单（占库存/成本）
GATE_BALANCE = "balance_received"        # 确认尾款到账（碰钱）


def auto_plan(db: Database, lead) -> list[dict]:
    """从 Lead 生成资源计划（启发式）：每城住 (天数/城数) 晚酒店 + 城际交通。
    只生成计划；价格库查不到的资源，计调 agent 会诚实报缺、不编价。"""
    cities = lead.cities or []
    if not cities:
        return []
    nights = max(1, (lead.duration_days or len(cities)) // len(cities))
    plan = [{"city": c, "resource_type": "hotel", "qty": nights} for c in cities]
    for i in range(len(cities) - 1):  # 城际交通，按人数
        plan.append({"city": cities[i], "resource_type": "transport",
                     "qty": lead.pax_count or 1})
    return plan


class CompanyPipeline:
    def __init__(self, db: Database, extractor,
                 payment_adapter=None, supplier_adapter=None):
        self.db = db
        self.cs = CustomerServiceAgent(db, extractor)
        self.ops = OperationsAgent(db)
        self.coll = CollectionAgent(db, payment_adapter)
        self.proc = ProcurementAgent(db, supplier_adapter)
        self.an = AnalyticsAgent(db)

    def run(self, conversation: str, approver: Callable[[str, dict], bool],
            source: str = "", needs_transport: bool = True,
            platform: str = "", region: str = "") -> dict:
        """把一条对话自动推到底，人审闸门处调 approver。返回完整轨迹。"""
        trace: list[dict] = []

        def step(name, **info):
            trace.append({"step": name, **info})

        # 1) 客服：对话 → Lead
        r = self.cs.ingest(conversation, source=source)
        lead_id = r["lead_id"]
        self.an.record_event("inquiry", lead_id=lead_id, platform=platform, region=region)
        step("intake", lead_id=lead_id, status=r["status"])
        if r["status"] != "qualified":
            step("need_more_info", missing=r["missing"], follow_up=r["follow_up"])
            return {"result": "collecting", "lead_id": lead_id, "trace": trace}
        self.an.record_event("valid", lead_id=lead_id, platform=platform, region=region)

        # 2) 计调：Lead + 价格库 → 报价
        lead = self.db.get_lead(lead_id)
        q = self.ops.build_quote(lead_id, auto_plan(self.db, lead))
        if q["status"] != "quoted":
            step("quote_blocked", reason=q["status"], missing=q.get("missing"))
            return {"result": "need_pricebook", "lead_id": lead_id,
                    "missing": q.get("missing"), "trace": trace}
        qid = q["quote_id"]
        self.an.record_event("quoted", lead_id=lead_id, platform=platform, region=region)
        step("quote_ready", quote_id=qid, price=q["price"])

        # 🔴 闸门1：发报价给客户前人审
        if not approver(GATE_SEND_QUOTE, {"quote_id": qid, "price": q["price"]}):
            step("stopped", gate=GATE_SEND_QUOTE)
            return {"result": "held", "gate": GATE_SEND_QUOTE, "trace": trace}
        self.db.conn.execute("UPDATE quotes SET status='sent' WHERE id=?", (qid,))
        self.db.conn.commit()
        step("quote_sent", quote_id=qid)

        # 3) 客户确认（外部）→ 建订单（定金规则）
        pct = costing.pick_deposit_pct(needs_transport)
        dep = costing.deposit_amount(q["price"], pct)
        oid = self.db.add_order(Order(quote_id=qid, needs_advance_transport=needs_transport,
                                      deposit_pct=pct, deposit_amount=dep,
                                      balance_amount=q["price"] - dep))
        step("order_created", order_id=oid, deposit_pct=pct, deposit=dep)

        # 收款：生成定金收款单
        self.coll.request_deposit(oid)
        # 🔴 闸门2：确认定金到账（碰钱，人做）
        if not approver(GATE_DEPOSIT, {"order_id": oid, "amount": dep}):
            step("stopped", gate=GATE_DEPOSIT)
            return {"result": "held", "gate": GATE_DEPOSIT, "order_id": oid, "trace": trace}
        self.coll.confirm_deposit_paid(oid)
        step("deposit_paid", order_id=oid)

        # 4) 供应商下单：定金后正式预订
        booking_ids = self.proc.plan_bookings(qid, oid)
        # 🔴 闸门3：向供应商正式下单（占库存/成本）
        if not approver(GATE_BOOK_SUPPLIERS, {"quote_id": qid, "bookings": booking_ids}):
            step("stopped", gate=GATE_BOOK_SUPPLIERS)
            return {"result": "held", "gate": GATE_BOOK_SUPPLIERS, "trace": trace}
        for bid in booking_ids:
            self.proc.confirm_booking(bid)
        step("suppliers_booked", bookings=len(booking_ids))

        # 5) 落地后收尾款
        self.coll.request_balance(oid)
        # 🔴 闸门4：确认尾款到账（碰钱）
        if not approver(GATE_BALANCE, {"order_id": oid, "amount": q["price"] - dep}):
            step("stopped", gate=GATE_BALANCE)
            return {"result": "held", "gate": GATE_BALANCE, "order_id": oid, "trace": trace}
        self.coll.confirm_balance_paid(oid)
        # 成交 → 记 funnel won
        self.an.record_event("won", lead_id=lead_id, amount=q["price"],
                             platform=platform, region=region)
        step("settled", order_id=oid, gmv=q["price"])

        return {"result": "settled", "lead_id": lead_id, "quote_id": qid,
                "order_id": oid, "gmv": q["price"], "trace": trace}
