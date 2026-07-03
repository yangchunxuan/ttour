"""供应商下单（Procurement）agent 核心 —— Phase 5。

职责（V2 报价流程第四阶段 + 二·3 多城市地接匹配）：客户付定金后，把报价里的
每项资源向对应供应商下单，协调多城市衔接。取消时按退改政策分档退款。

🔴 §3 业务闸门：
- 创建 booking 记录 = 计划(pending)，随时可做。
- 真下单 confirm_booking = 占真实库存/产生成本 → 守卫「订单定金已付」+ 人审的
  可插拔 SupplierAdapter(requires_human_confirm)。agent 不自动对供应商承诺。
具体供应商系统/网页的真对接是往 SupplierAdapter 插实现，本核心不含。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol, Optional

from company.record.db import Database
from company.record.refund import refund_pct


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SupplierAdapter(Protocol):
    requires_human_confirm: bool

    def place_booking(self, booking: dict) -> dict:
        """向供应商下单（真实现操作供应商系统/网页 = 人审）。"""
        ...


class StubSupplierAdapter:
    """占位：只生成一张下单指令，不碰真供应商系统。"""
    requires_human_confirm = True

    def place_booking(self, booking: dict) -> dict:
        return {"instruction": f"向供应商#{booking['supplier_id']} 下单 "
                               f"{booking['city']}/{booking['resource_type']}×{booking['qty']}",
                "requires_human_confirm": True}


class ProcurementAgent:
    def __init__(self, db: Database, adapter: SupplierAdapter | None = None):
        self.db = db
        self.adapter = adapter or StubSupplierAdapter()

    def plan_bookings(self, quote_id: int, order_id: Optional[int] = None) -> list[int]:
        """从报价明细生成 pending booking 记录（计划，不下单）。"""
        items = self.db.conn.execute(
            "SELECT * FROM quote_items WHERE quote_id=?", (quote_id,)
        ).fetchall()
        ids = []
        for it in items:
            pb = self.db.get_price(it["price_book_id"])
            cur = self.db.conn.execute(
                "INSERT INTO bookings(quote_id,order_id,price_book_id,supplier_id,city,"
                "resource_type,qty,cost,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (quote_id, order_id, it["price_book_id"], pb["supplier_id"], it["city"],
                 it["resource_type"], it["qty"], it["line_cost"], "pending", _now()))
            ids.append(cur.lastrowid)
        self.db.conn.commit()
        return ids

    def _order_deposit_paid(self, order_id: Optional[int]) -> bool:
        if order_id is None:
            return False
        o = self.db.conn.execute("SELECT deposit_status FROM orders WHERE id=?",
                                 (order_id,)).fetchone()
        return bool(o) and o["deposit_status"] == "paid"

    def confirm_booking(self, booking_id: int) -> dict:
        """真下单：守卫定金已付 + 人审。pending→confirmed。"""
        b = self.db.conn.execute("SELECT * FROM bookings WHERE id=?",
                                 (booking_id,)).fetchone()
        if b is None:
            raise ValueError(f"booking {booking_id} 不存在")
        if not self._order_deposit_paid(b["order_id"]):
            raise ValueError("定金未付，不能向供应商正式下单（V2：付定金后才预订资源）")
        req = self.adapter.place_booking(dict(b))
        self.db.conn.execute("UPDATE bookings SET status='confirmed' WHERE id=?",
                             (booking_id,))
        self.db.conn.commit()
        return {"booking_id": booking_id, "status": "confirmed", **req}

    def cancel_booking(self, booking_id: int, days_before: int) -> dict:
        """取消：按退改政策分档退款（V2 三·4）。政策没配就诚实说未配置。"""
        b = self.db.conn.execute("SELECT * FROM bookings WHERE id=?",
                                 (booking_id,)).fetchone()
        if b is None:
            raise ValueError(f"booking {booking_id} 不存在")
        pct = refund_pct(self.db, b["resource_type"], days_before)
        self.db.conn.execute("UPDATE bookings SET status='cancelled' WHERE id=?",
                             (booking_id,))
        self.db.conn.commit()
        if pct is None:
            return {"booking_id": booking_id, "status": "cancelled",
                    "refund": None, "note": "该资源退改政策未配置（待补数字），需人工核定"}
        refund = round(b["cost"] * pct / 100)
        return {"booking_id": booking_id, "status": "cancelled",
                "refund_pct": pct, "refund_amount": refund}

    def summary(self, quote_id: int) -> dict:
        rows = self.db.conn.execute(
            "SELECT status, COUNT(*) c FROM bookings WHERE quote_id=? GROUP BY status",
            (quote_id,)).fetchall()
        return {r["status"]: r["c"] for r in rows}
