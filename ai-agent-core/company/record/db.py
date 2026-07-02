"""数据访问层：唯一的读写入口。所有写入在边界处过 validate 的不变量。

设计原则：agent 只能通过这里读写真相源；护栏（尤其 INV-Q1 不许编价）在这里
强制，agent 绕不过去。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import Supplier, PriceEntry, Lead, Quote, QuoteItem, Order
from . import validate, costing

_SCHEMA = Path(__file__).with_name("schema.sql")
_DEFAULT_DB = Path(__file__).with_name("data") / "company.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else _DEFAULT_DB
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(_SCHEMA.read_text(encoding="utf-8"))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ---------------- suppliers / price_book ---------------- #
    def add_supplier(self, s: Supplier) -> int:
        cur = self.conn.execute(
            "INSERT INTO suppliers(name,city_coverage,types,price_level,service_score,"
            "stability,emergency,feedback_notes,status,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (s.name, json.dumps(s.city_coverage, ensure_ascii=False),
             json.dumps(s.types), s.price_level, s.service_score, s.stability,
             s.emergency, s.feedback_notes, s.status, _now(), _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def add_price(self, p: PriceEntry) -> int:
        cur = self.conn.execute(
            "INSERT INTO price_book(city,resource_type,supplier_id,spec,date_start,"
            "date_end,cost,currency,source,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (p.city, p.resource_type, p.supplier_id, json.dumps(p.spec, ensure_ascii=False),
             p.date_start, p.date_end, p.cost, p.currency, p.source, _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_price(self, price_book_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM price_book WHERE id=?", (price_book_id,)
        ).fetchone()

    def query_price(self, city: str, resource_type: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM price_book WHERE city=? AND resource_type=?",
            (city, resource_type),
        ).fetchall()

    # ---------------- leads ---------------- #
    def add_lead(self, l: Lead) -> int:
        cur = self.conn.execute(
            "INSERT INTO leads(source,pax_count,ages,depart_date,duration_days,cities,"
            "has_flight,has_budget,budget_amount,special_requests,hotel_level,room_bed_pref,"
            "guide_need,car_need,intent,convo_summary,status,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (l.source, l.pax_count, l.ages, l.depart_date, l.duration_days,
             json.dumps(l.cities, ensure_ascii=False),
             None if l.has_flight is None else int(l.has_flight),
             None if l.has_budget is None else int(l.has_budget),
             l.budget_amount, l.special_requests, l.hotel_level, l.room_bed_pref,
             l.guide_need, l.car_need, l.intent, l.convo_summary, l.status, _now()),
        )
        self.conn.commit()
        l.id = cur.lastrowid
        return l.id

    def get_lead(self, lead_id: int) -> Optional[Lead]:
        row = self.conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        if not row:
            return None
        return Lead(
            id=row["id"], source=row["source"], pax_count=row["pax_count"], ages=row["ages"],
            depart_date=row["depart_date"], duration_days=row["duration_days"],
            cities=json.loads(row["cities"] or "[]"),
            has_flight=None if row["has_flight"] is None else bool(row["has_flight"]),
            has_budget=None if row["has_budget"] is None else bool(row["has_budget"]),
            budget_amount=row["budget_amount"], special_requests=row["special_requests"],
            hotel_level=row["hotel_level"], room_bed_pref=row["room_bed_pref"],
            guide_need=row["guide_need"], car_need=row["car_need"], intent=row["intent"],
            convo_summary=row["convo_summary"], status=row["status"],
        )

    # ---------------- quotes（护栏在这里） ---------------- #
    def add_quote(self, q: Quote, items: list[QuoteItem],
                  require_lead_complete: bool = True) -> int:
        # INV-L1：lead 必须信息齐才能报价
        if require_lead_complete:
            lead = self.get_lead(q.lead_id)
            if lead is None:
                raise validate.ValidationError("INV-L1", f"lead {q.lead_id} 不存在")
            validate.validate_lead_complete(lead)

        # INV-Q1：每条明细必须引用真实 price_book 条目，且单价一致
        for it in items:
            pb = self.get_price(it.price_book_id)
            if pb is None:
                raise validate.ValidationError(
                    "INV-Q1",
                    f"报价项引用了不存在的价格条目 price_book_id={it.price_book_id} "
                    f"→ 只能引用真实 price_book 条目，别编价",
                )
            validate.validate_item_price(it, pb["cost"])
            it.line_cost = costing.line_cost(it)

        # INV-Q2：total_cost 必须 == 明细确定性汇总
        validate.validate_total_cost(q, items)
        # INV-Q3 / INV-Q4：承诺标注 + 报价性质
        validate.validate_quote_commitments(q)
        validate.validate_quote_nature(q)

        cur = self.conn.execute(
            "INSERT INTO quotes(lead_id,itinerary,total_cost,margin,quote_price,currency,"
            "commitments,nature,version,parent_quote_id,status,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (q.lead_id, json.dumps(q.itinerary, ensure_ascii=False), q.total_cost, q.margin,
             q.quote_price, q.currency, json.dumps(q.commitments, ensure_ascii=False),
             q.nature, q.version, q.parent_quote_id, q.status, _now()),
        )
        qid = cur.lastrowid
        for it in items:
            self.conn.execute(
                "INSERT INTO quote_items(quote_id,price_book_id,city,resource_type,qty,"
                "unit_cost,line_cost,note) VALUES(?,?,?,?,?,?,?,?)",
                (qid, it.price_book_id, it.city, it.resource_type, it.qty,
                 it.unit_cost, it.line_cost, it.note),
            )
        self.conn.commit()
        q.id = qid
        return qid

    # ---------------- orders（护栏在这里） ---------------- #
    def add_order(self, o: Order) -> int:
        validate.validate_deposit(o.needs_advance_transport, o.deposit_pct)  # INV-O1
        cur = self.conn.execute(
            "INSERT INTO orders(quote_id,needs_advance_transport,deposit_pct,deposit_amount,"
            "pay_method,deposit_status,balance_amount,balance_status,refund_policy_snapshot,"
            "status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (o.quote_id, int(o.needs_advance_transport), o.deposit_pct, o.deposit_amount,
             o.pay_method, o.deposit_status, o.balance_amount, o.balance_status,
             json.dumps(o.refund_policy_snapshot, ensure_ascii=False), o.status, _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    # ---------------- 全库不变量巡检（cli check 用） ---------------- #
    def check_all(self) -> list[str]:
        """跑一遍全库不变量，返回违规描述列表（空=全绿）。只读。"""
        problems: list[str] = []
        # 每张报价单：明细引用真实、单价一致、总成本一致、承诺/性质
        for qrow in self.conn.execute("SELECT * FROM quotes").fetchall():
            items = []
            for irow in self.conn.execute(
                "SELECT * FROM quote_items WHERE quote_id=?", (qrow["id"],)
            ).fetchall():
                it = QuoteItem(price_book_id=irow["price_book_id"], city=irow["city"],
                               resource_type=irow["resource_type"], qty=irow["qty"],
                               unit_cost=irow["unit_cost"], line_cost=irow["line_cost"])
                pb = self.get_price(it.price_book_id)
                try:
                    if pb is None:
                        raise validate.ValidationError("INV-Q1", f"quote {qrow['id']} 引用不存在价格条目")
                    validate.validate_item_price(it, pb["cost"])
                except validate.ValidationError as e:
                    problems.append(str(e))
                items.append(it)
            q = Quote(lead_id=qrow["lead_id"], total_cost=qrow["total_cost"],
                      commitments=json.loads(qrow["commitments"] or "{}"),
                      nature=qrow["nature"])
            for fn in (lambda: validate.validate_total_cost(q, items),
                       lambda: validate.validate_quote_commitments(q),
                       lambda: validate.validate_quote_nature(q)):
                try:
                    fn()
                except validate.ValidationError as e:
                    problems.append(f"quote {qrow['id']}: {e}")
        # 订单：定金规则
        for orow in self.conn.execute("SELECT * FROM orders").fetchall():
            try:
                validate.validate_deposit(bool(orow["needs_advance_transport"]), orow["deposit_pct"])
            except validate.ValidationError as e:
                problems.append(f"order {orow['id']}: {e}")
        return problems
