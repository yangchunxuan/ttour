"""数据分析 agent 核心测试（离线，纯读 funnel 表）。"""

from __future__ import annotations

import pytest

from company.record.db import Database
from company.roles.analytics import AnalyticsAgent

Y = 100


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    yield d
    d.close()


def _seed_funnel(a: AnalyticsAgent):
    # facebook：3 咨询 → 1 成单（成交 8000 元）
    for i in range(3):
        a.record_event("inquiry", lead_id=100 + i, platform="facebook", region="US")
    a.record_event("valid", lead_id=100, platform="facebook", region="US")
    a.record_event("quoted", lead_id=100, platform="facebook", region="US")
    a.record_event("won", lead_id=100, amount=8000 * Y, platform="facebook", region="US")
    # instagram：2 咨询 → 0 成单
    for i in range(2):
        a.record_event("inquiry", lead_id=200 + i, platform="instagram", region="EU")


def test_funnel_summary_math(db):
    a = AnalyticsAgent(db)
    _seed_funnel(a)
    s = a.funnel_summary()
    assert s["counts"]["inquiry"] == 5   # 3 fb + 2 ig
    assert s["counts"]["won"] == 1
    assert s["gmv"] == 8000 * Y
    assert s["rates"]["won/inquiry"] == round(1 / 5, 4)


def test_by_dimension_platform(db):
    a = AnalyticsAgent(db)
    _seed_funnel(a)
    rows = a.by_dimension("platform")
    fb = next(r for r in rows if r["platform"] == "facebook")
    ig = next(r for r in rows if r["platform"] == "instagram")
    assert fb["inquiry"] == 3 and fb["won"] == 1 and fb["won_rate"] == round(1 / 3, 4)
    assert ig["inquiry"] == 2 and ig["won"] == 0 and ig["won_rate"] == 0.0
    # facebook 转化更高，排前面
    assert rows[0]["platform"] == "facebook"


def test_optimization_hints(db):
    a = AnalyticsAgent(db)
    _seed_funnel(a)
    hints = a.optimization_hints("platform")
    assert any("facebook" in h and "加投" in h for h in hints)
    assert any("instagram" in h and "收缩" in h for h in hints)


def test_empty_funnel(db):
    a = AnalyticsAgent(db)
    s = a.funnel_summary()
    assert s["counts"]["inquiry"] == 0 and s["rates"]["won/inquiry"] == 0.0
    assert a.optimization_hints("platform") == ["数据不足，暂无优化建议"]
