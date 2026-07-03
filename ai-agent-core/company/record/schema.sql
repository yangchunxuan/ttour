-- 系统真相源 DDL（Phase 0）。人 + agent 可直接读这份文件了解数据结构。
-- 全部纯 SQLite。金额单位分（整数，避免浮点），币种字段标 currency。

-- 供应商库：匹配 agent 靠 price_level × service_score 做「成本×服务」平衡（V2 二·4）
CREATE TABLE IF NOT EXISTS suppliers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    city_coverage TEXT NOT NULL DEFAULT '[]',   -- json: ["北京","西安"]
    types         TEXT NOT NULL DEFAULT '[]',   -- json: ["hotel","dmc"]（resource_type 多选）
    price_level   INTEGER NOT NULL DEFAULT 3,   -- 1(便宜)~5(贵)
    service_score INTEGER NOT NULL DEFAULT 3,   -- 1~5 服务质量
    stability     INTEGER NOT NULL DEFAULT 3,   -- 1~5 稳定性
    emergency     INTEGER NOT NULL DEFAULT 3,   -- 1~5 应急能力
    feedback_notes TEXT DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'active', -- active/paused
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

-- 价格库：成本按 城市 × 资源类型 × 供应商 × 日期段 存。国内交通(高铁/机票)是 resource_type=transport
CREATE TABLE IF NOT EXISTS price_book (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    city          TEXT NOT NULL,
    resource_type TEXT NOT NULL,   -- hotel/car/guide/ticket/dmc/transport
    supplier_id   INTEGER NOT NULL REFERENCES suppliers(id),
    spec          TEXT NOT NULL DEFAULT '{}', -- json: 酒店{star,room_type,bed_type}；transport{mode,from,to}
    date_start    TEXT NOT NULL,   -- YYYY-MM-DD 日期段起
    date_end      TEXT NOT NULL,   -- 日期段止
    cost          INTEGER NOT NULL, -- 成本（分）
    currency      TEXT NOT NULL DEFAULT 'CNY',
    source        TEXT DEFAULT '',
    updated_at    TEXT NOT NULL
);

-- 线索/需求：客服 agent 收集的 8 项基础信息（V2 一·2）
CREATE TABLE IF NOT EXISTS leads (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT DEFAULT '',    -- SaleSmartly 会话 id
    pax_count     INTEGER,            -- 人数
    ages          TEXT,               -- 年龄描述
    depart_date   TEXT,               -- 出行日期
    duration_days INTEGER,            -- 天数
    cities        TEXT DEFAULT '[]',  -- json 有序：想去城市
    has_flight    INTEGER,            -- 是否已有机票 0/1
    has_budget    INTEGER,            -- 是否有预算 0/1
    budget_amount INTEGER,            -- 预算（分），has_budget=1 时必填
    special_requests TEXT DEFAULT '',
    hotel_level   TEXT DEFAULT '',
    room_bed_pref TEXT DEFAULT '',
    guide_need    TEXT DEFAULT '',
    car_need      TEXT DEFAULT '',
    intent        TEXT DEFAULT 'low', -- low/mid/high
    convo_summary TEXT DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'new', -- new/collecting/qualified/quoted/won/lost
    created_at    TEXT NOT NULL
);

-- 报价单
CREATE TABLE IF NOT EXISTS quotes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id       INTEGER NOT NULL REFERENCES leads(id),
    itinerary     TEXT NOT NULL DEFAULT '[]',  -- json: [{city,nights,route_order}]
    total_cost    INTEGER NOT NULL DEFAULT 0,  -- 由 costing 汇总（分）
    margin        INTEGER NOT NULL DEFAULT 0,  -- 利润（分）
    quote_price   INTEGER NOT NULL DEFAULT 0,  -- 对客价（分）= total_cost + margin
    currency      TEXT NOT NULL DEFAULT 'CNY',
    commitments   TEXT NOT NULL DEFAULT '{}',  -- json: {guaranteed:[...], not_guaranteed:[...]}
    nature        TEXT NOT NULL DEFAULT 'estimate', -- 恒为预估
    version       INTEGER NOT NULL DEFAULT 1,  -- 1=首价, 2=二次报价...
    parent_quote_id INTEGER REFERENCES quotes(id),
    status        TEXT NOT NULL DEFAULT 'draft', -- draft/pending_review/sent/confirmed/rejected
    created_at    TEXT NOT NULL
);

-- 报价明细：每条必须引用真实 price_book 条目（INV-Q1）
CREATE TABLE IF NOT EXISTS quote_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id      INTEGER NOT NULL REFERENCES quotes(id),
    price_book_id INTEGER NOT NULL REFERENCES price_book(id),
    city          TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    qty           INTEGER NOT NULL DEFAULT 1,
    unit_cost     INTEGER NOT NULL,   -- 下单快照，必须 == price_book.cost（INV-Q1）
    line_cost     INTEGER NOT NULL,   -- = unit_cost * qty
    note          TEXT DEFAULT ''
);

-- 订单/收款
CREATE TABLE IF NOT EXISTS orders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id      INTEGER NOT NULL REFERENCES quotes(id),
    needs_advance_transport INTEGER NOT NULL DEFAULT 0, -- 是否需提前订国内交通（决定 30/40）
    deposit_pct   INTEGER NOT NULL,   -- 只能 30 或 40（INV-O1）
    deposit_amount INTEGER NOT NULL,
    pay_method    TEXT NOT NULL DEFAULT 'alipay', -- alipay/paypal/other
    deposit_status TEXT NOT NULL DEFAULT 'unpaid', -- unpaid/paid
    deposit_ref   TEXT DEFAULT '',    -- 支付方收款单号（如 PayPal 发票 id），对账用
    balance_amount INTEGER NOT NULL DEFAULT 0,
    balance_status TEXT NOT NULL DEFAULT 'unpaid',
    balance_ref   TEXT DEFAULT '',    -- 支付方尾款收款单号，对账用
    refund_policy_snapshot TEXT DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'created', -- created/deposit_paid/arranged/landed/settled/cancelled
    created_at    TEXT NOT NULL
);

-- 退改政策（V2 三·4，用户说要完善）：按资源类型 + 距入住天数分档。数字待用户给
CREATE TABLE IF NOT EXISTS refund_policy (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_type TEXT NOT NULL,      -- hotel/flight/train/ticket/guide/car/dmc
    days_before_min INTEGER NOT NULL, -- 距入住天数下界（含）
    days_before_max INTEGER,          -- 上界（NULL=无上限）
    refund_pct    INTEGER NOT NULL,   -- 退款百分比 0~100
    note          TEXT DEFAULT ''
);

-- 转化数据（V2 一·3）
CREATE TABLE IF NOT EXISTS funnel_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type    TEXT NOT NULL,      -- inquiry/valid/quoted/won
    -- lead_id 是可选关联，不设硬外键：咨询(inquiry)事件在真实业务里往往
    -- 还没建成结构化 lead（咨询人数 ≠ lead 数），强制外键会误杀。
    lead_id       INTEGER,
    amount        INTEGER DEFAULT 0,  -- 成交金额（分），won 才有
    ad_content    TEXT DEFAULT '',
    region        TEXT DEFAULT '',
    campaign_time TEXT DEFAULT '',
    platform      TEXT DEFAULT '',
    created_at    TEXT NOT NULL
);

-- 定价/引流策略配置（V2 末尾「低价引流+二次报价」）。值待用户给
CREATE TABLE IF NOT EXISTS pricing_strategy (
    key           TEXT PRIMARY KEY,   -- markup_default / lowball_discount / requote_trigger
    value         TEXT NOT NULL,
    note          TEXT DEFAULT ''
);

-- 供应商下单记录（V2 报价流程第四阶段「客户支付定金后公司开始正式预订资源」）
-- 🔴 创建=计划(pending)；真下单(confirmed)要定金已付 + 人审（占真库存/产生成本）
CREATE TABLE IF NOT EXISTS bookings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id      INTEGER NOT NULL REFERENCES quotes(id),
    order_id      INTEGER REFERENCES orders(id),
    price_book_id INTEGER NOT NULL REFERENCES price_book(id),
    supplier_id   INTEGER NOT NULL REFERENCES suppliers(id),
    city          TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    qty           INTEGER NOT NULL DEFAULT 1,
    cost          INTEGER NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending', -- pending/confirmed/cancelled
    created_at    TEXT NOT NULL
);
