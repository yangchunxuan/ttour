"""数据模型：枚举 + dataclass（类型化，agent/人可读）。

金额一律用「分」的整数存，避免浮点误差。资源类型/状态用 enum 锁死取值。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ResourceType(str, Enum):
    HOTEL = "hotel"
    CAR = "car"
    GUIDE = "guide"
    TICKET = "ticket"
    DMC = "dmc"           # 地接
    TRANSPORT = "transport"  # 国内交通：高铁 / 国内机票


class LeadStatus(str, Enum):
    NEW = "new"
    COLLECTING = "collecting"
    QUALIFIED = "qualified"
    QUOTED = "quoted"
    WON = "won"
    LOST = "lost"


class QuoteStatus(str, Enum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    SENT = "sent"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class OrderStatus(str, Enum):
    CREATED = "created"
    DEPOSIT_PAID = "deposit_paid"
    ARRANGED = "arranged"
    LANDED = "landed"
    SETTLED = "settled"
    CANCELLED = "cancelled"


class PayMethod(str, Enum):
    ALIPAY = "alipay"
    PAYPAL = "paypal"
    OTHER = "other"


# 报价性质恒为「预估」（V2 三·1）
QUOTE_NATURE_ESTIMATE = "estimate"

# 定金只能这两档（V2 四）
VALID_DEPOSIT_PCT = (30, 40)


@dataclass
class Supplier:
    name: str
    city_coverage: list[str] = field(default_factory=list)
    types: list[str] = field(default_factory=list)
    price_level: int = 3
    service_score: int = 3
    stability: int = 3
    emergency: int = 3
    feedback_notes: str = ""
    status: str = "active"
    id: Optional[int] = None


@dataclass
class PriceEntry:
    city: str
    resource_type: str          # ResourceType 值
    supplier_id: int
    date_start: str             # YYYY-MM-DD
    date_end: str
    cost: int                   # 分
    spec: dict = field(default_factory=dict)
    currency: str = "CNY"
    source: str = ""
    id: Optional[int] = None


@dataclass
class Lead:
    source: str = ""
    platform: str = ""          # 归一投放平台（facebook/instagram/…），漏斗按此统计
    pax_count: Optional[int] = None
    ages: Optional[str] = None
    depart_date: Optional[str] = None
    duration_days: Optional[int] = None
    cities: list[str] = field(default_factory=list)
    has_flight: Optional[bool] = None
    has_budget: Optional[bool] = None
    budget_amount: Optional[int] = None
    special_requests: str = ""
    hotel_level: str = ""
    room_bed_pref: str = ""
    guide_need: str = ""
    car_need: str = ""
    intent: str = "low"
    convo_summary: str = ""
    status: str = LeadStatus.NEW.value
    id: Optional[int] = None


@dataclass
class QuoteItem:
    price_book_id: int
    city: str
    resource_type: str
    qty: int
    unit_cost: int              # 必须 == price_book.cost（INV-Q1）
    line_cost: int = 0          # = unit_cost * qty，由 costing 算
    note: str = ""
    quote_id: Optional[int] = None
    id: Optional[int] = None


@dataclass
class Quote:
    lead_id: int
    itinerary: list[dict] = field(default_factory=list)
    total_cost: int = 0
    margin: int = 0
    quote_price: int = 0
    currency: str = "CNY"
    # commitments 必须区分能承诺 vs 不能承诺（INV-Q3 / V2 三·2/3）
    commitments: dict = field(default_factory=lambda: {"guaranteed": [], "not_guaranteed": []})
    nature: str = QUOTE_NATURE_ESTIMATE
    version: int = 1
    parent_quote_id: Optional[int] = None
    status: str = QuoteStatus.DRAFT.value
    id: Optional[int] = None


@dataclass
class Order:
    quote_id: int
    needs_advance_transport: bool
    deposit_pct: int            # 30 或 40（INV-O1）
    deposit_amount: int
    pay_method: str = PayMethod.ALIPAY.value
    deposit_status: str = "unpaid"
    balance_amount: int = 0
    balance_status: str = "unpaid"
    refund_policy_snapshot: dict = field(default_factory=dict)
    status: str = OrderStatus.CREATED.value
    id: Optional[int] = None
