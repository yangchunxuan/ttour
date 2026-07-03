"""价格表导入器测试：真数据灌进价格库 → 计调出真报价；坏行诚实跳过不编价。"""

from __future__ import annotations

import pytest

from company.record.db import Database
from company.systems.import_data import import_price_book, _parse_spec, _to_fen
from company.roles.operations import OperationsAgent
from company.record.models import Lead, LeadStatus

GOOD_ROWS = [
    {"supplier_name": "北京饭店", "city": "北京", "resource_type": "hotel",
     "spec": "star=5;bed=大床", "date_start": "2026-09-01", "date_end": "2026-12-31",
     "cost_yuan": "800", "service_score": "4", "stability": "4", "price_level": "3"},
    {"supplier_name": "北京饭店", "city": "北京", "resource_type": "transport",
     "spec": "", "date_start": "2026-09-01", "date_end": "2026-12-31",
     "cost_yuan": "550.50"},
    {"supplier_name": "西安宾馆", "city": "西安", "resource_type": "hotel",
     "spec": "star=5", "date_start": "2026-09-01", "date_end": "2026-12-31",
     "cost_yuan": "650"},
]


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "imp.db")
    yield d
    d.close()


def test_import_loads_prices_and_dedupes_supplier(db):
    r = import_price_book(db, GOOD_ROWS)
    assert r["prices_added"] == 3
    assert r["suppliers_created"] == 2          # 北京饭店 两行 → 一个供应商
    assert r["errors"] == []
    # 真进了价格库，元→分正确
    pb = db.query_price("北京", "transport")
    assert pb and pb[0]["cost"] == 55050        # 550.50 元
    assert db.query_price("北京", "hotel")[0]["cost"] == 80000


def test_imported_prices_produce_real_quote(db):
    """灌完真价 → 计调 build_quote 出真报价（不再是示例）。"""
    import_price_book(db, GOOD_ROWS)
    lead_id = db.add_lead(Lead(source="t", pax_count=2, ages="35", depart_date="2026-10-05",
                               duration_days=4, cities=["北京", "西安"], has_flight=False,
                               has_budget=True, budget_amount=2000000,
                               status=LeadStatus.QUALIFIED.value))
    plan = [{"city": "北京", "resource_type": "hotel", "qty": 2},
            {"city": "西安", "resource_type": "hotel", "qty": 2}]
    q = OperationsAgent(db).build_quote(lead_id, plan, markup=0.2)
    assert q["status"] == "quoted"
    # 成本 = (800*2 + 650*2)*100 = 290000 分；加价 20%
    assert q["total"] == 290000 and q["price"] == int(290000 * 1.2)


def test_bad_rows_skipped_honestly(db):
    rows = [
        {"supplier_name": "X", "city": "北京", "resource_type": "flight",  # 非法类型
         "date_start": "2026-09-01", "date_end": "2026-12-31", "cost_yuan": "100"},
        {"supplier_name": "Y", "city": "北京", "resource_type": "hotel",   # 成本非数字
         "date_start": "2026-09-01", "date_end": "2026-12-31", "cost_yuan": "abc"},
        {"supplier_name": "Z", "city": "北京", "resource_type": "hotel",   # 缺日期
         "date_start": "", "date_end": "", "cost_yuan": "100"},
        {"supplier_name": "# 注释行", "city": "", "resource_type": ""},     # 注释跳过
    ]
    r = import_price_book(db, rows)
    assert r["prices_added"] == 0
    assert len(r["errors"]) == 3                # 注释行不算错误
    assert any("flight" in e for e in r["errors"])


def test_shipped_template_file_loads(db):
    """随附的模板文件本身能被导入器读（示例行入库、注释行跳过）。"""
    from pathlib import Path
    tmpl = Path(__file__).resolve().parents[1] / "onboarding" / "price_book_template.csv"
    r = import_price_book(db, str(tmpl))
    assert r["prices_added"] == 3 and r["suppliers_created"] == 3   # 模板 3 个不同供应商
    assert r["errors"] == []


def test_helpers():
    assert _parse_spec("star=5;bed=大床") == {"star": "5", "bed": "大床"}
    assert _parse_spec("") == {}
    assert _to_fen("800") == 80000 and _to_fen("12.34") == 1234
    with pytest.raises(ValueError):
        _to_fen("0")
