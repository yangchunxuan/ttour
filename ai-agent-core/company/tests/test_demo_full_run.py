"""彩排全跑验收：证明整个公司能在合成数据上完整自转（多客户→报价→收款→结清→反馈），
且人审闸门与信息不全分支都如实生效。"""

from __future__ import annotations

from company.demo_full_run import run_dress_rehearsal


def test_full_company_runs_end_to_end():
    logs = []
    s = run_dress_rehearsal(log=logs.append)

    # 4 位客户：2 成交 / 1 被人审在供应商闸门按住 / 1 信息不全停在收集
    assert s["customers"] == 4
    assert s["settled"] == 2 and s["held"] == 1 and s["collecting"] == 1
    assert s["gmv"] > 0

    # 漏斗自洽：咨询4 → 有效3 → 报价3 → 成单2
    f = s["funnel"]["counts"]
    assert f == {"inquiry": 4, "valid": 3, "quoted": 3, "won": 2}
    assert s["funnel"]["gmv"] == s["gmv"]

    # 成交的那两单，轨迹确实跑完了收款闭环
    for r in s["results"]:
        if r["result"] == "settled":
            for step in ("quote_ready", "quote_sent", "deposit_paid",
                         "suppliers_booked", "settled"):
                assert step in r["steps"]


def test_held_order_did_not_settle():
    s = run_dress_rehearsal(log=lambda *a: None)
    held = [r for r in s["results"] if r["result"] == "held"]
    assert held and all("settled" not in r["steps"] for r in held)
