"""company/systems/import_data.py —— 把你的真价格表一条命令灌进价格库。

作用：我造不出你的真实价格，但能让「你把价格交给我」变成填一张 CSV + 跑一条命令。
灌进去之后，计调 agent 出的报价就是**真价**（不再是示例）——这是把整套系统从
占位切到真实运营的第一个、也是最关键的插头（对应 V2 二·2 计调报价核算）。

CSV 列（见 company/onboarding/price_book_template.csv）：
  supplier_name, city, resource_type, spec, date_start, date_end, cost_yuan,
  service_score, stability, price_level, currency(可选,默认CNY), source(可选)
  · resource_type ∈ {hotel, car, guide, ticket, dmc, transport}
  · spec：可留空，或 "star=5;bed=大床" 这种 k=v;k=v（给酒店星级/床型等）
  · cost_yuan：成本（人民币元，整数或两位小数），入库自动 ×100 存分
  · 同一 supplier_name 多行 → 只建一个供应商，累积其覆盖城市/资源类型

诚实原则：格式不对/类型不认/成本非数字的行 **跳过并报错**，绝不猜、绝不编价。

跑：python3 -m company.systems.import_data prices.csv
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, Union

from company.record.db import Database
from company.record.models import Supplier, PriceEntry, ResourceType

VALID_TYPES = {t.value for t in ResourceType}

REQUIRED_COLS = ("supplier_name", "city", "resource_type", "date_start",
                 "date_end", "cost_yuan")


def _parse_spec(raw: str) -> dict:
    raw = (raw or "").strip()
    if not raw:
        return {}
    out: dict = {}
    for pair in raw.replace("，", ";").replace(",", ";").split(";"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _to_fen(raw: str) -> int:
    """元 → 分。非数字/≤0 抛错（诚实：不编价）。"""
    val = float(str(raw).strip())
    if val <= 0:
        raise ValueError(f"成本必须 >0，收到 {raw!r}")
    return round(val * 100)


def import_price_book(db: Database,
                      source: Union[str, Path, Iterable[dict]]) -> dict:
    """把价格表灌进 suppliers + price_book。返回 {suppliers_created, prices_added, errors}。

    source 可以是 CSV 路径，或一串 dict 行（便于测试/程序化调用）。
    """
    if isinstance(source, (str, Path)):
        with open(source, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    else:
        rows = list(source)

    supplier_ids: dict[str, int] = {}     # name → id（去重）
    prices_added = 0
    errors: list[str] = []

    for i, row in enumerate(rows, start=1):
        # 跳过空行/注释行（模板里以 # 开头的示例）
        name = (row.get("supplier_name") or "").strip()
        if not name or name.startswith("#"):
            continue
        missing = [c for c in REQUIRED_COLS if not (row.get(c) or "").strip()]
        if missing:
            errors.append(f"第{i}行 缺列 {missing} → 跳过")
            continue
        rtype = (row.get("resource_type") or "").strip()
        if rtype not in VALID_TYPES:
            errors.append(f"第{i}行 resource_type={rtype!r} 不在 {sorted(VALID_TYPES)} → 跳过")
            continue
        try:
            cost = _to_fen(row["cost_yuan"])
        except ValueError as e:
            errors.append(f"第{i}行 成本非法（{e}）→ 跳过")
            continue

        city = row["city"].strip()
        # 供应商：按名字去重，首次见到才建
        if name not in supplier_ids:
            supplier_ids[name] = db.add_supplier(Supplier(
                name=name, city_coverage=[city], types=[rtype],
                service_score=int(row.get("service_score") or 3),
                stability=int(row.get("stability") or 3),
                price_level=int(row.get("price_level") or 3)))
        db.add_price(PriceEntry(
            city=city, resource_type=rtype, supplier_id=supplier_ids[name],
            spec=_parse_spec(row.get("spec", "")),
            date_start=row["date_start"].strip(), date_end=row["date_end"].strip(),
            cost=cost, currency=(row.get("currency") or "CNY").strip(),
            source=(row.get("source") or "import").strip()))
        prices_added += 1

    return {"suppliers_created": len(supplier_ids), "prices_added": prices_added,
            "errors": errors}


def main(argv=None) -> int:
    import argparse
    import json
    ap = argparse.ArgumentParser(description="把真价格表灌进价格库")
    ap.add_argument("csv", help="价格表 CSV 路径（见 onboarding/price_book_template.csv）")
    args = ap.parse_args(argv)
    db = Database()
    out = import_price_book(db, args.csv)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\n✅ 灌入 {out['prices_added']} 条价格、{out['suppliers_created']} 个供应商。"
          f"{'⚠️ 有 '+str(len(out['errors']))+' 行被跳过，见上。' if out['errors'] else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
