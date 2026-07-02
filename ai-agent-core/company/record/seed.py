"""灌几条结构真实的示例数据，走完整校验链路——证明护栏放行合法数据。

跑：python3 -m company.record.seed
"""

from __future__ import annotations

from pathlib import Path

from .db import Database
from .models import (Supplier, PriceEntry, Lead, Quote, QuoteItem, Order,
                     ResourceType, LeadStatus)
from . import costing

# 金额单位：分（1 元 = 100 分）
Y = 100


def seed(path: Path | None = None) -> Database:
    db = Database(path)

    # 供应商：北京一家地接+酒店资源，服务好偏中价
    sup_bj = db.add_supplier(Supplier(
        name="北京优选地接", city_coverage=["北京"], types=["hotel", "dmc", "car", "guide"],
        price_level=3, service_score=4, stability=4, emergency=4))
    sup_xa = db.add_supplier(Supplier(
        name="西安丝路地接", city_coverage=["西安"], types=["hotel", "dmc", "ticket"],
        price_level=2, service_score=4, stability=3, emergency=3))

    # 价格库：北京 5 星酒店/晚、用车/天、地接服务；西安酒店；高铁北京→西安
    d0, d1 = "2026-09-01", "2026-12-31"
    pb_hotel_bj = db.add_price(PriceEntry(
        city="北京", resource_type=ResourceType.HOTEL.value, supplier_id=sup_bj,
        spec={"star": 5, "room_type": "标准间", "bed_type": "大床"},
        date_start=d0, date_end=d1, cost=800 * Y))          # 800 元/晚
    pb_car_bj = db.add_price(PriceEntry(
        city="北京", resource_type=ResourceType.CAR.value, supplier_id=sup_bj,
        spec={"type": "商务7座"}, date_start=d0, date_end=d1, cost=600 * Y))  # 600 元/天
    pb_hotel_xa = db.add_price(PriceEntry(
        city="西安", resource_type=ResourceType.HOTEL.value, supplier_id=sup_xa,
        spec={"star": 5, "room_type": "标准间", "bed_type": "大床"},
        date_start=d0, date_end=d1, cost=650 * Y))
    pb_train = db.add_price(PriceEntry(
        city="北京", resource_type=ResourceType.TRANSPORT.value, supplier_id=sup_bj,
        spec={"mode": "高铁", "from": "北京", "to": "西安"},
        date_start=d0, date_end=d1, cost=550 * Y))          # 550 元/人

    # 一条信息齐全的线索（8 项都有）
    lead_id = db.add_lead(Lead(
        source="ss-conv-1001", pax_count=2, ages="35,33", depart_date="2026-10-05",
        duration_days=5, cities=["北京", "西安"], has_flight=True, has_budget=True,
        budget_amount=20000 * Y, hotel_level="5星", intent=LeadStatus.QUALIFIED.value,
        status="qualified"))

    # 基于真实价格库条目组一张报价（北京住3晚+用车2天+高铁2人；西安住2晚）
    items = [
        QuoteItem(price_book_id=pb_hotel_bj, city="北京", resource_type="hotel",
                  unit_cost=800 * Y, qty=3),
        QuoteItem(price_book_id=pb_car_bj, city="北京", resource_type="car",
                  unit_cost=600 * Y, qty=2),
        QuoteItem(price_book_id=pb_train, city="北京", resource_type="transport",
                  unit_cost=550 * Y, qty=2),
        QuoteItem(price_book_id=pb_hotel_xa, city="西安", resource_type="hotel",
                  unit_cost=650 * Y, qty=2),
    ]
    total = costing.roll_up(items)                    # 确定性汇总
    margin, price = costing.apply_margin(total, 0.20)  # 加 20%
    q = Quote(
        lead_id=lead_id,
        itinerary=[{"city": "北京", "nights": 3, "route_order": 1},
                   {"city": "西安", "nights": 2, "route_order": 2}],
        total_cost=total, margin=margin, quote_price=price,
        commitments={
            "guaranteed": ["酒店5星", "房型标准间", "床型大床"],
            "not_guaranteed": ["具体酒店名称", "是否能加床", "临近日期房间库存"],
        },
    )
    qid = db.add_quote(q, items)  # 走 INV-Q1/Q2/Q3/Q4 全部校验

    # 订单：含高铁需提前订 → 40% 定金
    needs_transport = True
    dep_pct = costing.pick_deposit_pct(needs_transport)
    order_id = db.add_order(Order(
        quote_id=qid, needs_advance_transport=needs_transport, deposit_pct=dep_pct,
        deposit_amount=costing.deposit_amount(price, dep_pct),
        balance_amount=price - costing.deposit_amount(price, dep_pct)))

    print("✅ seed 完成（全部走护栏校验）")
    print(f"  供应商 2 家、价格库 4 条、线索 #{lead_id}")
    print(f"  报价 #{qid}：总成本 {total/Y:.0f} 元 + 利润 {margin/Y:.0f} 元 = 对客价 {price/Y:.0f} 元")
    print(f"  订单 #{order_id}：{dep_pct}% 定金 = {costing.deposit_amount(price, dep_pct)/Y:.0f} 元")
    return db


if __name__ == "__main__":
    seed()
