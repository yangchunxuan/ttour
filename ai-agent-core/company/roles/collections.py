"""收款（Collections）agent 核心 —— Phase 4。

职责（V2 四）：按定金规则生成收款单（30%/40%）→ 对账 → 落地后收尾款。

🔴 最高安全原则（§3 业务闸门）：**agent 绝不自动划钱/确认到账**。
- agent 只做：算金额、生成收款单/指令、维护订单状态机、对账。
- 「真去支付页操作 / 确认真收到钱」= 人审的可插拔适配器（PaymentAdapter），
  真实现里 requires_human_confirm=True，由人在支付后端确认。
- confirm_*_paid() 代表「人/系统已核实到账」，不是 agent 自己把钱划了。

不依赖具体支付系统：支付宝/PayPal 的真对接是往 PaymentAdapter 插实现，本核心不含。
"""

from __future__ import annotations

from typing import Protocol

from company.record.db import Database
from company.record.models import OrderStatus, PayMethod
from company.record import costing


class PaymentAdapter(Protocol):
    """支付适配器协议。真实现（支付宝/PayPal）里操作真支付页 = 人审。"""
    requires_human_confirm: bool

    def create_payment_request(self, amount: int, method: str, memo: str) -> dict:
        """生成收款单/链接（不划钱）。返回给客户的收款指令。"""
        ...


class StubPaymentAdapter:
    """占位适配器：只生成一张"收款单"文本，不碰任何真支付系统。"""
    requires_human_confirm = True

    def create_payment_request(self, amount: int, method: str, memo: str) -> dict:
        return {
            "amount_yuan": amount / 100,
            "method": method,
            "memo": memo,
            "instruction": f"请通过 {method} 支付 {amount/100:.0f} 元（{memo}）",
            "requires_human_confirm": True,   # 到账确认要人做
        }


class CollectionAgent:
    def __init__(self, db: Database, adapter: PaymentAdapter | None = None):
        self.db = db
        self.adapter = adapter or StubPaymentAdapter()

    def _order(self, order_id: int):
        return self.db.conn.execute(
            "SELECT * FROM orders WHERE id=?", (order_id,)
        ).fetchone()

    # ---- 定金 ---- #
    def request_deposit(self, order_id: int) -> dict:
        """生成定金收款单（不划钱）。金额来自订单（Phase 0 已按 30/40 规则算好）。"""
        o = self._order(order_id)
        if o is None:
            raise ValueError(f"order {order_id} 不存在")
        req = self.adapter.create_payment_request(
            o["deposit_amount"], o["pay_method"], f"订单#{order_id} {o['deposit_pct']}% 定金")
        # 记下支付方收款单号（如 PayPal 发票 id）→ 后续可自动对账
        if req.get("provider_ref"):
            self.db.conn.execute("UPDATE orders SET deposit_ref=? WHERE id=?",
                                 (req["provider_ref"], order_id))
            self.db.conn.commit()
        return {"order_id": order_id, "kind": "deposit", **req}

    def confirm_deposit_paid(self, order_id: int) -> dict:
        """人/系统核实定金到账后调用 → 订单进 deposit_paid。agent 不自己确认划钱。"""
        o = self._order(order_id)
        if o["deposit_status"] == "paid":
            return {"order_id": order_id, "status": o["status"], "note": "已是已付"}
        self.db.conn.execute(
            "UPDATE orders SET deposit_status='paid', status=? WHERE id=?",
            (OrderStatus.DEPOSIT_PAID.value, order_id))
        self.db.conn.commit()
        return {"order_id": order_id, "status": OrderStatus.DEPOSIT_PAID.value}

    # ---- 尾款（落地后）---- #
    def request_balance(self, order_id: int) -> dict:
        """生成尾款收款单。守卫：必须定金已付（状态机）。"""
        o = self._order(order_id)
        if o["deposit_status"] != "paid":
            raise ValueError("定金未付，不能收尾款（状态机守卫）")
        req = self.adapter.create_payment_request(
            o["balance_amount"], o["pay_method"], f"订单#{order_id} 尾款")
        if req.get("provider_ref"):
            self.db.conn.execute("UPDATE orders SET balance_ref=? WHERE id=?",
                                 (req["provider_ref"], order_id))
            self.db.conn.commit()
        return {"order_id": order_id, "kind": "balance", **req}

    def confirm_balance_paid(self, order_id: int) -> dict:
        """人核实尾款到账 → 订单结清 settled。守卫：定金必须先付。"""
        o = self._order(order_id)
        if o["deposit_status"] != "paid":
            raise ValueError("定金未付，尾款不该先到（状态机守卫）")
        self.db.conn.execute(
            "UPDATE orders SET balance_status='paid', status=? WHERE id=?",
            (OrderStatus.SETTLED.value, order_id))
        self.db.conn.commit()
        return {"order_id": order_id, "status": OrderStatus.SETTLED.value}

    # ---- 自动对账：读支付方权威状态，自动推进（去掉手工「确认到账」这一步）---- #
    # §3 说明：这**不是** agent 擅自认钱——是读支付方(PayPal)自己的 PAID 记录（到账的
    # 权威来源）来反映事实。对外承诺动作（发报价/向供应商下单）仍留人审，不受此影响。
    def reconcile_order(self, order_id: int) -> dict:
        """查支付方状态，若定金/尾款已 PAID 则自动推进订单。返回本次推进了什么。"""
        check = getattr(self.adapter, "check_status", None)
        if not callable(check):
            return {"order_id": order_id, "advanced": [],
                    "note": "支付适配器不支持状态查询 → 保持人工确认"}
        o = self._order(order_id)
        if o is None:
            raise ValueError(f"order {order_id} 不存在")
        advanced = []
        # 定金
        if o["deposit_status"] != "paid" and o["deposit_ref"]:
            if check(o["deposit_ref"]).get("paid"):
                self.confirm_deposit_paid(order_id)
                advanced.append("deposit")
                o = self._order(order_id)
        # 尾款（须定金已付）
        if o["deposit_status"] == "paid" and o["balance_status"] != "paid" and o["balance_ref"]:
            if check(o["balance_ref"]).get("paid"):
                self.confirm_balance_paid(order_id)
                advanced.append("balance")
        return {"order_id": order_id, "advanced": advanced}

    def reconcile_all(self) -> dict:
        """对所有「有支付方单号且未收齐」的订单跑一遍自动对账。"""
        check = getattr(self.adapter, "check_status", None)
        if not callable(check):
            return {"reconciled": [], "note": "支付适配器不支持状态查询 → 全靠人工确认"}
        rows = self.db.conn.execute(
            "SELECT id FROM orders WHERE (deposit_status='unpaid' AND deposit_ref!='') "
            "OR (deposit_status='paid' AND balance_status='unpaid' AND balance_ref!='')"
        ).fetchall()
        results = [self.reconcile_order(r["id"]) for r in rows]
        return {"checked": len(results),
                "reconciled": [r for r in results if r.get("advanced")]}

    def reconcile(self) -> dict:
        """对账：各状态订单数 + 已收/待收金额。只读。"""
        rows = self.db.conn.execute("SELECT * FROM orders").fetchall()
        collected = sum((r["deposit_amount"] if r["deposit_status"] == "paid" else 0) +
                        (r["balance_amount"] if r["balance_status"] == "paid" else 0)
                        for r in rows)
        outstanding = sum((r["deposit_amount"] if r["deposit_status"] != "paid" else 0) +
                          (r["balance_amount"] if r["balance_status"] != "paid" else 0)
                          for r in rows)
        by_status: dict[str, int] = {}
        for r in rows:
            by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        return {"orders": len(rows), "collected": collected,
                "outstanding": outstanding, "by_status": by_status}
