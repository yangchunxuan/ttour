"""company.record —— 自主定制游 agent 公司的系统真相源（Phase 0）。

结构化、agent 可读可查、边界强校验的数据层：价格库 / 供应商 / 需求 / 报价 /
订单 / 转化 / 退改政策 / 定价策略。核心护栏 = 报价每项成本必须来自价格库真实
条目（INV-Q1，不许编价）。纯 stdlib，不碰 VM / 钱 / 网络。
"""

from .db import Database
from .validate import ValidationError

__all__ = ["Database", "ValidationError"]
