"""company/demo_full_run.py —— 「彩排全跑」：让整个公司在合成数据上完整自转一遍。

目的：证明这台机器**真的能运营一家公司**——不是零件、不是 --once 空库，而是从
「客户进来」一路自主跑到「收款结清 + 数据反馈」。只有两处是演示替身，其余全是真代码：
  · 价格库 = 合成演示数据（清楚标注；换成你的真 CSV 就是真价）
  · 外部 I/O（PayPal HTTP）= 假件（不碰网络/不划钱）；SaleSmartly 消息 = 脚本模拟
业务逻辑、护栏、闸门、状态机、算钱、对账、漏斗、投放建议——全是生产代码本身。

跑：python3 -m company.demo_full_run
"""

from __future__ import annotations

from company.record.db import Database
from company.systems.import_data import import_price_book
from company.systems.salesmartly import SaleSmartlyInbound, parse_webhook
from company.pipeline import (CompanyPipeline, GATE_BOOK_SUPPLIERS)
from company.systems.paypal import PayPalPaymentAdapter
from company.roles.collections import CollectionAgent
from company.roles.marketing import MarketingAgent
from company.roles.analytics import AnalyticsAgent

# ---- 合成价格库（演示数据；换成你的真 CSV 即真价）---- #
DEMO_PRICES = [
    {"supplier_name": "北京华龙地接", "city": "北京", "resource_type": "hotel",
     "spec": "star=5", "date_start": "2026-09-01", "date_end": "2026-12-31",
     "cost_yuan": "820", "service_score": "5", "stability": "5", "price_level": "4"},
    {"supplier_name": "北京华龙地接", "city": "北京", "resource_type": "transport",
     "date_start": "2026-09-01", "date_end": "2026-12-31", "cost_yuan": "560"},
    {"supplier_name": "北京华龙地接", "city": "北京", "resource_type": "guide",
     "date_start": "2026-09-01", "date_end": "2026-12-31", "cost_yuan": "600"},
    {"supplier_name": "西安唐韵", "city": "西安", "resource_type": "hotel",
     "spec": "star=5", "date_start": "2026-09-01", "date_end": "2026-12-31",
     "cost_yuan": "650", "service_score": "4", "stability": "4", "price_level": "3"},
    {"supplier_name": "西安唐韵", "city": "西安", "resource_type": "transport",
     "date_start": "2026-09-01", "date_end": "2026-12-31", "cost_yuan": "480"},
    {"supplier_name": "成都天府", "city": "成都", "resource_type": "hotel",
     "spec": "star=5", "date_start": "2026-09-01", "date_end": "2026-12-31",
     "cost_yuan": "700", "service_score": "4", "stability": "5", "price_level": "3"},
]

# ---- 合成客户（不同画像；换成真 SaleSmartly 消息即真客户）---- #
CUSTOMERS = [
    {"name": "Anna (facebook)", "platform": "facebook", "needs_transport": True,
     "hold_supplier_gate": False,
     "fields": {"pax_count": 2, "ages": "35,33", "depart_date": "2026-10-05",
                "duration_days": 4, "cities": ["北京", "西安"], "has_flight": False,
                "has_budget": True, "budget_amount": 22000, "hotel_level": "5星", "intent": "high"}},
    {"name": "Ben (instagram)", "platform": "instagram", "needs_transport": True,
     "hold_supplier_gate": False,
     "fields": {"pax_count": 3, "ages": "40,38,10", "depart_date": "2026-11-02",
                "duration_days": 6, "cities": ["北京", "西安", "成都"], "has_flight": True,
                "has_budget": True, "budget_amount": 40000, "hotel_level": "5星", "intent": "high"}},
    {"name": "Cara (facebook, 价格敏感)", "platform": "facebook", "needs_transport": False,
     "hold_supplier_gate": True,   # 演示：人审在供应商闸门按住不放
     "fields": {"pax_count": 2, "ages": "28,29", "depart_date": "2026-10-20",
                "duration_days": 3, "cities": ["北京"], "has_flight": True,
                "has_budget": True, "budget_amount": 9000, "hotel_level": "5星", "intent": "mid"}},
    {"name": "Dee (instagram, 信息不全)", "platform": "instagram", "needs_transport": True,
     "hold_supplier_gate": False,
     "fields": {"pax_count": 2, "cities": ["上海"]}},   # 缺日期/天数 → 停在收集
]


def _plan(cities, pax, days):
    n = max(1, days // len(cities))
    plan = [{"city": c, "resource_type": "hotel", "qty": n} for c in cities]
    for i in range(len(cities) - 1):
        plan.append({"city": cities[i], "resource_type": "transport", "qty": pax})
    return plan


def run_dress_rehearsal(log=print) -> dict:
    db = Database(":memory:")
    fake_pp = _FakePayPal()
    adapter = PayPalPaymentAdapter(currency="CNY", transport=fake_pp)

    imp = import_price_book(db, DEMO_PRICES)
    log(f"① 灌入合成价格库：{imp['prices_added']} 条价、{imp['suppliers_created']} 个供应商\n")

    results = []
    for c in CUSTOMERS:
        log(f"── 客户 {c['name']} 进来（{c['platform']}）──")
        # 前门：SaleSmartly 入站（脚本模拟一条消息）→ 客服抽需求 → Lead
        inbound = SaleSmartlyInbound(db, lambda conv, s, f=c["fields"]: dict(f))
        r = inbound.handle(parse_webhook({
            "event": "message", "chat_user_id": c["name"], "chat_session_id": c["name"],
            "sequence_id": 1, "msg": "（客户咨询）", "msg_type": "text",
            "channel": 1 if c["platform"] == "facebook" else 5, "send_time": "1"}))
        # 咨询(inquiry)由前门 SaleSmartlyInbound 自动记（客户进门那一刻）——见 salesmartly.py
        if r["status"] != "qualified":
            log(f"   客服：信息不全（缺 {r['missing']}）→ 追问「{r['follow_up']}」，停在收集。\n")
            results.append({"customer": c["name"], "result": "collecting"})
            continue

        # 后续自主流程：计调报价 → 闸门 → 定金(PayPal发票) → 对账 → 下单 → 尾款 → 结清
        pipe = CompanyPipeline(db, extractor=lambda conv, s, f=c["fields"]: dict(f),
                               payment_adapter=adapter)

        def approver(gate, ctx, hold=c["hold_supplier_gate"]):
            if hold and gate == GATE_BOOK_SUPPLIERS:
                return False   # 人审：这一单先按住不向供应商下单
            return True

        out = pipe.run_from_lead(r["lead_id"], approver=approver,
                                 needs_transport=c["needs_transport"], platform=c["platform"])
        # 若走到收款，客户在 PayPal 付款 → autopilot 式自动对账推进
        coll = CollectionAgent(db, adapter)
        for inv in list(fake_pp.issued):
            fake_pp.paid.add(inv)
        rec = coll.reconcile_all()

        steps = [t["step"] for t in out["trace"]]
        if out["result"] == "settled":
            log(f"   ✅ 全自动跑通：报价{out['gmv']/100:.0f}元 → 定金→下单→尾款→结清（成交）")
        elif out["result"] == "held":
            log(f"   ⏸️ 停在人审闸门「{out['gate']}」：自动化推到闸门就交人拍板（§3），未继续。")
        else:
            log(f"   ↩︎ {out['result']}：{out.get('missing')}")
        results.append({"customer": c["name"], "result": out["result"],
                        "gmv": out.get("gmv", 0), "steps": steps})
        log("")

    # 数据反馈：转化漏斗 + 投放优化建议（V2 一·3）
    funnel = AnalyticsAgent(db).funnel_summary()
    recos = MarketingAgent(db).budget_recommendations("platform")
    log("② 数据反馈（自动统计）：")
    log(f"   漏斗 咨询{funnel['counts']['inquiry']}→有效{funnel['counts']['valid']}"
        f"→报价{funnel['counts']['quoted']}→成单{funnel['counts']['won']}"
        f"，成交额 {funnel['gmv']/100:.0f} 元，总转化率 {funnel['rates']['won/inquiry']:.0%}")
    for rr in recos:
        log(f"   投放建议：{rr['dim']}={rr['value']} → {rr['action']}（{rr['reason']}）")

    settled = [x for x in results if x["result"] == "settled"]
    summary = {"customers": len(CUSTOMERS), "settled": len(settled),
               "held": len([x for x in results if x["result"] == "held"]),
               "collecting": len([x for x in results if x["result"] == "collecting"]),
               "gmv": funnel["gmv"], "funnel": funnel, "results": results}
    db.close()
    return summary


class _FakePayPal:
    def __init__(self):
        self.issued = []
        self.paid = set()
        self._n = 0

    def __call__(self, method, path, payload):
        if path == "/v2/invoicing/invoices":
            self._n += 1
            inv = f"INV-{self._n}"
            self.issued.append(inv)
            return {"id": inv}
        if method == "GET":
            inv = path.rsplit("/", 1)[-1]
            return {"id": inv, "status": "PAID" if inv in self.paid else "SENT"}
        return {}


def main() -> int:
    print("=" * 66)
    print(" 孤岛世界之外 · 定制游公司 —— 彩排全跑（合成数据，业务逻辑全真）")
    print("=" * 66 + "\n")
    s = run_dress_rehearsal()
    print("\n" + "=" * 66)
    print(f" 结果：{s['customers']} 位客户 → 成交 {s['settled']} / 人审按住 {s['held']} / "
          f"信息不全 {s['collecting']}，GMV {s['gmv']/100:.0f} 元")
    print(" 换成你的真价格表 + 真账号，这一整套就是真运营。")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
