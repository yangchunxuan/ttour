# 工单 Phase 0：系统真相源（system of record）

> 交付对象：Codex / Gemini。验收人：Claude（按 §6 逐条真跑）。
> 上位文档：`company/docs/ARCHITECTURE.md`（业务层建在 `../macos_agent/` 引擎上）。业务权威规格：用户口述的《前段获客与转化工作流程 V2》。
> **这是整个 agent 公司的地基**：把"价格库/供应商/需求/报价/订单/转化"变成**结构化、agent 可读可查、边界强校验**的数据。没有它，计调 agent 算报价只能瞎编。
> **本工单是纯数据层代码，Codex 在普通开发环境写即可——不需要 VM、不碰钱、不联网。**（业务 agent 跑起来时才在 VM 里，那是后续 Phase。）

---

## 0. 铁律
1. **纯标准库**：只用 Python 3.11 stdlib（`sqlite3` / `dataclasses` / `enum` / `datetime` / `json`）。不装任何第三方包。
2. **不碰 VM、不碰钱、不联网**：这层只读写本地 SQLite，不发消息、不付款、不调外部 API。
3. **边界强校验（护栏，最重要）**：数据写入/读出的边界处强制不变量（见 §3）。核心是 **"报价里每一项成本必须来自价格库的真实条目，不许凭空编价"**——这是防"假报价"的硬判据，等价于 macos_agent 里"文件必须真存在"。
4. **agent 可读**：schema 清晰、字段自解释、有注释；一个 agent（或人）不看别的文档也能懂这张表是干嘛、每个字段什么意思。
5. **诚实占位**：那 4 个待定业务事实（价格数据源 / SaleSmartly 接法 / 退改分档 / 定价策略）是**往表里填值/填配置**，不改结构。结构里为它们留好位置（如 `refund_policy` 表、`pricing_strategy` 配置），值先留空或示例。

---

## 1. 交付物
```
company/record/
├─ __init__.py
├─ schema.sql            # 建表 DDL（真相源之一，人+agent 可直接读）
├─ models.py             # 每张表的 dataclass + enum（类型化）
├─ db.py                 # 连接 + CRUD + 边界校验（唯一读写入口）
├─ validate.py           # §3 不变量校验（被 db.py 调用，也可单独跑）
├─ costing.py            # 确定性成本汇总（把 quote_items 加成总成本——纯算术，非 LLM）
├─ seed.py               # 灌几条结构真实的示例数据（供测试/agent 起步）
├─ cli.py                # 命令行查询/巡检（python3 -m company.record.cli ...）
├─ data/                 # SQLite 落这里（gitignore）
└─ tests/test_record.py  # §6 的反例测试
```

## 2. 数据表（基于 V2 业务规格）
> 字段给"必须有"的核心，Codex 可按需补辅助字段。全部带 `id`（主键）、`created_at`/`updated_at`。

### 2.1 `suppliers` 供应商库
`id, name, city_coverage(城市列表 json), types(hotel/car/guide/ticket/dmc/transport 多选), price_level(1-5), service_score(1-5), stability(1-5), emergency(1-5), feedback_notes, status(active/paused)`
> 匹配 agent 靠 `price_level × service_score` 做"成本×服务"平衡（V2 二·4：不选最便宜也不选最贵）。

### 2.2 `price_book` 价格库
`id, city, resource_type(enum: hotel/car/guide/ticket/dmc/transport), supplier_id(FK), spec(json: 酒店→{star,room_type,bed_type}；transport→{mode:高铁/国内机票,from,to}；其余按需), date_range_start, date_range_end, cost, currency, source, updated_at`
> 成本按 `城市 × 资源类型 × 供应商 × 日期段` 存。国内交通(高铁/国内机票)是一种 resource_type=transport。

### 2.3 `leads` 线索/需求
8 项基础信息（V2 一·2）：`id, source(SaleSmartly 会话id), pax_count(人数), ages(年龄), depart_date(出行日期), duration_days(天数), cities(想去城市 json 有序), has_flight(是否已有机票 bool), has_budget(bool), budget_amount, special_requests, hotel_level, room_bed_pref, guide_need, car_need, intent(意向分级 low/mid/high), convo_summary, status(new/collecting/qualified/quoted/won/lost), created_at`

### 2.4 `quotes` 报价单 + `quote_items` 明细
- `quotes`: `id, lead_id(FK), itinerary(json: [{city, nights, route_order}]), total_cost, margin, quote_price(含利润的对客价), margin_pct, nature('estimate' 恒为预估), version(1=首价,2=二次报价…), parent_quote_id(二次报价指向首价), commitments(json: {guaranteed:[star,room_type,bed_type], not_guaranteed:[specific_hotel, extra_bed, late_inventory]}), status(draft/pending_review/sent/confirmed/rejected), created_at`
- `quote_items`: `id, quote_id(FK), price_book_id(FK 必填且必须存在), city, resource_type, qty, unit_cost(下单时快照), line_cost, note`
> **护栏**：每条 quote_item 必须引用一条**真实存在**的 price_book 行（§3 INV-Q1）。total_cost 由 costing.py 从 items 确定性汇总，不是 LLM 填的。

### 2.5 `orders` 订单/收款
`id, quote_id(FK), deposit_pct(只能 30 或 40), deposit_amount, needs_advance_transport(bool 决定 30/40), pay_method(alipay/paypal/other), deposit_status(unpaid/paid), balance_amount, balance_status, refund_policy_snapshot(json 下单时的退改政策快照), status(created/deposit_paid/arranged/landed/settled/cancelled), created_at`

### 2.6 `refund_policy` 退改政策（V2 三·4 用户说要完善）
按资源类型 + 距入住天数分档：`id, resource_type(hotel/flight/train/ticket/guide/car/dmc), days_before_min, days_before_max, refund_pct, note`
> 结构先建好，**具体数字待用户给**（seed 里放酒店的示例：≥15天可取消/成本小、<15天承担部分成本；其余资源按各自政策——先留占位）。

### 2.7 `funnel_events` 转化数据（V2 一·3）
`id, event_type(inquiry/valid/quoted/won), lead_id, amount(成交金额, won 才有), ad_content, region, campaign_time, platform, created_at`
> 数据 agent 从这里统计 咨询→有效→报价→成单→转化率，按 广告内容/地区/时间/平台 维度切。

### 2.8 `pricing_strategy` 定价/引流策略配置（V2 末尾"低价引流+二次报价"）
键值配置表：`key, value, note`。先留键位：`markup_default`(默认加价率), `lowball_discount`(首价压低幅度), `requote_trigger`(什么条件触发二次报价)。**值待用户给**。

---

## 3. 边界不变量（validate.py，护栏）
| id | 判据 | 拒绝时报什么 |
|---|---|---|
| **INV-Q1 报价不许编价** | 每条 `quote_item.price_book_id` 必须能在 price_book 查到；且 `unit_cost` 与该条目 cost 一致（或在允许漂移内） | "报价项引用了不存在的价格条目 / 单价与价格库不符 → 只能引用真实 price_book 条目，别编价" |
| **INV-Q2 总成本确定性** | `quotes.total_cost` == costing.py 从 items 汇总的值 | "total_cost 与明细汇总不符 → 用 costing.roll_up() 计算，别手填" |
| **INV-O1 定金规则** | `orders.deposit_pct ∈ {30,40}`；`needs_advance_transport=True → 必须 40`，否则默认 30（V2 四） | "定金比例只能 30/40，且需提前订国内交通时必须 40" |
| **INV-L1 需求完整性** | lead 要推进到 quoted，8 项基础信息必须齐（人数/年龄/日期/天数/城市/是否有机票/是否有预算/预算或明确无预算） | "线索缺 X 字段，不能进入报价阶段——先让客服 agent 补齐" |
| **INV-Q3 承诺标注** | quote.commitments 必须区分 guaranteed(星级/房型/床型) vs not_guaranteed(具体酒店/加床/临期库存)（V2 三·2/3） | "报价必须标清能承诺 vs 不能承诺，别把不保证的说成保证" |
| **INV-Q4 报价性质** | `quotes.nature == 'estimate'`（预估，非锁死）；二次报价用 version+parent_quote_id | "报价性质恒为预估" |

## 4. costing.py（确定性成本汇总——不是 LLM 的活）
- `roll_up(quote_items) -> total_cost`：把明细行 line_cost 加总。
- `apply_margin(total_cost, strategy) -> quote_price`：按 pricing_strategy 加价率算对客价。
- `pick_deposit_pct(lead/quote) -> 30|40`：按"是否需提前订国内交通"判定。
- 这些是**确定性算术**，日后 LLM agent 只负责"选哪个供应商/定什么策略"，钱的加总由这里算，防幻觉。

## 5. seed.py + cli.py
- `seed.py`：灌 2~3 个城市、几家供应商、对应 price_book、1 条示例 lead → 生成 1 张示例 quote（走真实校验链路，证明护栏放行合法数据）。
- `cli.py`：`query-price <city> <type>` / `list-leads` / `build-quote <lead_id>`（用 costing 算一张报价草稿）/ `check`（跑全部 §3 不变量巡检整库）。

## 6. 验收标准（Claude 逐条真跑）
| # | 标准 | 判法 |
|---|---|---|
| V1 | 建库可跑 | `python3 -m company.record.seed` 建库+灌数据无错；`schema.sql` 人可读 |
| V2 | 护栏真拦"编价" | 造一条引用不存在 price_book_id 的 quote_item → db.py 拒绝，报 INV-Q1；单价不符也拒 |
| V3 | 总成本确定性 | 手改 total_cost 与明细不符 → INV-Q2 拦；costing.roll_up 结果正确 |
| V4 | 定金规则 | needs_advance_transport=True 存 30 → INV-O1 拦；默认 30/需交通 40 正确 |
| V5 | 需求完整性 | 缺字段的 lead 尝试进 quoted → INV-L1 拦 |
| V6 | 承诺标注 | quote 缺 commitments 区分 → INV-Q3 拦 |
| V7 | 每条不变量有反例测试 | tests/ 里每个 INV-xx 配一个注入违规→被拒的用例 |
| V8 | 纯 stdlib 离线 | 无第三方 import、无网络；`python3 -m company.record.cli check` 能跑 |
| V9 | agent 可读 | 每张表/字段有注释；一个不看别的文档的 agent 能看懂 build-quote 的输入输出 |

## 7. 明确不做（后续 Phase）
- 不接 SaleSmartly、不发消息、不付款、不匹配真实供应商网页——那些是业务 agent 在 VM 里干的（Phase 1+）。
- 本 Phase 只把"真相"结构化好，让后面的 agent 有可靠地基可站。
