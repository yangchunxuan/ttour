"""市场投放优化 agent 测试：全阶段维度漏斗 + 可执行预算建议 + 调仓 + 反馈报告。
纯确定性统计，喂造好的 funnel_events。"""

from __future__ import annotations

import pytest

from company.record.db import Database
from company.roles.marketing import MarketingAgent, StubAdSpendAdapter
from company.roles.analytics import AnalyticsAgent


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "m.db")
    yield d
    d.close()


def _seed(db):
    """两平台对比：facebook 转化好，instagram 咨询多但 0 成交。"""
    an = AnalyticsAgent(db)
    # facebook：3 咨询 → 2 有效 → 2 报价 → 1 成交(2万元)
    for lid in (1, 2, 3):
        an.record_event("inquiry", lead_id=lid, platform="facebook")
    for lid in (1, 2):
        an.record_event("valid", lead_id=lid, platform="facebook")
        an.record_event("quoted", lead_id=lid, platform="facebook")
    an.record_event("won", lead_id=1, amount=2000000, platform="facebook")
    # instagram：4 咨询 → 1 有效 → 0 报价 → 0 成交
    for lid in (4, 5, 6, 7):
        an.record_event("inquiry", lead_id=lid, platform="instagram")
    an.record_event("valid", lead_id=4, platform="instagram")


def test_dimension_funnel_all_stages(db):
    _seed(db)
    fn = MarketingAgent(db).dimension_funnel("platform")
    fb = next(x for x in fn if x["platform"] == "facebook")
    ig = next(x for x in fn if x["platform"] == "instagram")
    assert (fb["inquiry"], fb["valid"], fb["quoted"], fb["won"]) == (3, 2, 2, 1)
    assert fb["gmv"] == 2000000
    assert (ig["inquiry"], ig["valid"], ig["quoted"], ig["won"]) == (4, 1, 0, 0)
    # facebook 总转化率更高 → 排在前
    assert fn[0]["platform"] == "facebook"
    assert fb["rates"]["won/inquiry"] == round(1 / 3, 4)


def test_budget_reco_scale_up_and_pause(db):
    _seed(db)
    recs = MarketingAgent(db).budget_recommendations("platform", min_inquiry=1)
    by = {r["value"]: r for r in recs}
    assert by["facebook"]["action"] == "SCALE_UP"      # 最优 → 加投
    assert by["instagram"]["action"] == "PAUSE"        # 4 咨询 0 成交 → 暂停
    assert "0 成交" in by["instagram"]["reason"]


def test_budget_reco_uses_roas_when_spend_given(db):
    _seed(db)
    # facebook 花 5000 元成交 2 万 → ROAS 4；instagram 花 3000 元成交 0 → ROAS 0
    spend = {"facebook": 500000, "instagram": 300000}
    recs = MarketingAgent(db).budget_recommendations("platform", spend_by_value=spend)
    fb = next(r for r in recs if r["value"] == "facebook")
    assert fb["roas"] == 4.0 and fb["cpa"] == 500000.0   # 每单成本=花费/成交数
    assert recs[0]["value"] == "facebook"                # 按 ROAS 排序，fb 第一
    assert fb["action"] == "SCALE_UP"


def test_reallocation_moves_from_worst_to_best(db):
    _seed(db)
    spend = {"facebook": 500000, "instagram": 300000}
    re = MarketingAgent(db).reallocation("platform", spend, shift_pct=30)
    assert re["to"] == "facebook" and re["from"] == "instagram"
    assert re["move_fen"] == 90000 and re["move_yuan"] == 900.0   # 3000*30%
    assert "挪" in re["instruction"]


def test_reallocation_none_without_enough_priced(db):
    _seed(db)
    assert MarketingAgent(db).reallocation("platform", {"facebook": 500000}) is None


def test_feedback_report_structure(db):
    _seed(db)
    rep = MarketingAgent(db).feedback_report(dims=("platform",))
    assert rep["overall"]["counts"] == {"inquiry": 7, "valid": 3, "quoted": 2, "won": 1}
    assert rep["overall"]["gmv"] == 2000000
    assert "platform" in rep["by_dimension"]
    assert rep["by_dimension"]["platform"]["recommendations"][0]["action"] == "SCALE_UP"


def test_ad_adapter_never_spends(db):
    """占位投放适配器只出待人执行的指令，不碰广告平台/钱。"""
    out = StubAdSpendAdapter().apply_budget_change("platform", "facebook", "SCALE_UP", "提20%")
    assert out["requires_human_confirm"] is True and "待人执行" in out["instruction"]


def test_empty_data_no_recommendations(db):
    assert MarketingAgent(db).budget_recommendations("platform") == []
    assert MarketingAgent(db).dimension_funnel("region") == []


def test_bad_dimension_rejected(db):
    with pytest.raises(ValueError):
        MarketingAgent(db).dimension_funnel("not_a_dim")
