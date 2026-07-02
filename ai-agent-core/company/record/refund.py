"""退改政策查询（V2 三·4，用户说要完善的「距入住 X 天退 Y%」）。

数字存在 refund_policy 表里（值待用户给）。这里只提供确定性查询：
给资源类型 + 距入住天数 → 退款百分比。没配政策就诚实返回 None（不瞎猜）。
"""

from __future__ import annotations

from typing import Optional

from .db import Database


def refund_pct(db: Database, resource_type: str, days_before: int) -> Optional[int]:
    """查退款百分比。没有匹配的政策档 → None（政策未配置，别编）。"""
    row = db.conn.execute(
        "SELECT refund_pct FROM refund_policy WHERE resource_type=? "
        "AND days_before_min<=? AND (days_before_max IS NULL OR days_before_max>=?) "
        "ORDER BY days_before_min DESC LIMIT 1",
        (resource_type, days_before, days_before),
    ).fetchone()
    return int(row["refund_pct"]) if row else None
