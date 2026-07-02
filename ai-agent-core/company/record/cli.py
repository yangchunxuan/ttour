"""命令行查询/巡检真相源。

  python3 -m company.record.cli query-price 北京 hotel
  python3 -m company.record.cli list-leads
  python3 -m company.record.cli check          # 跑全库不变量巡检
"""

from __future__ import annotations

import argparse
import json

from .db import Database


def main() -> int:
    ap = argparse.ArgumentParser(description="company.record 真相源 CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_qp = sub.add_parser("query-price", help="查某城某资源类型的价格库条目")
    p_qp.add_argument("city")
    p_qp.add_argument("resource_type")
    sub.add_parser("list-leads", help="列出所有线索")
    sub.add_parser("list-quotes", help="列出所有报价")
    sub.add_parser("check", help="跑全库不变量巡检（只读）")
    args = ap.parse_args()

    db = Database()
    if args.cmd == "query-price":
        rows = db.query_price(args.city, args.resource_type)
        if not rows:
            print("（无匹配条目）")
        for r in rows:
            print(f"#{r['id']} {r['city']} {r['resource_type']} "
                  f"cost={r['cost']/100:.0f}元 spec={r['spec']} 供应商{r['supplier_id']}")
    elif args.cmd == "list-leads":
        for r in db.conn.execute("SELECT * FROM leads").fetchall():
            print(f"#{r['id']} {r['status']} 人数={r['pax_count']} 城市={r['cities']} "
                  f"日期={r['depart_date']} 意向={r['intent']}")
    elif args.cmd == "list-quotes":
        for r in db.conn.execute("SELECT * FROM quotes").fetchall():
            print(f"#{r['id']} lead={r['lead_id']} 对客价={r['quote_price']/100:.0f}元 "
                  f"性质={r['nature']} v{r['version']} 状态={r['status']}")
    elif args.cmd == "check":
        problems = db.check_all()
        if not problems:
            print("✅ 全库不变量巡检通过（0 违规）")
            return 0
        print(f"❌ 发现 {len(problems)} 处违规：")
        for p in problems:
            print("  " + p)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
