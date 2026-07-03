# 运维手册：怎么把这家公司「开起来并一直转」

`GO-LIVE.md` 讲你要**给什么**（价格/凭证/退改数字）。这份讲你要**怎么跑**——把
autopilot 跑成一个常驻进程、让 SaleSmartly 能打进来、以及你每天盯哪几个人审动作。

---

## 一、机器自己做什么 vs 你做什么

| 环节 | 谁做 | 说明 |
|---|---|---|
| 客户消息进来 → 抽需求 → 建 Lead | 🤖 自动 | SaleSmartly webhook → 客服 agent |
| 合格 Lead → 生成预估报价 | 🤖 自动 | autopilot 每轮把合格客户自动报价（待人审） |
| **把报价发给客户** | 🧑 你 | §3 对外承诺 → `console send <quote_id>` |
| 定金收款单（PayPal 发票） | 🤖 自动 | send 时生成 |
| 定金到账 → 推进订单 | 🤖 自动 | autopilot 读 PayPal PAID 状态自动对账 |
| **向供应商正式下单** | 🧑 你 | §3 占库存/成本 → `console book <order_id>` |
| 客户落地 → 收尾款 | 🧑 你 | 「落地」是现实事件 → `console balance <order_id>` |
| 尾款到账 → 结清 → 记成交 | 🤖 自动 | autopilot 对账 |
| 转化漏斗 + 投放优化建议 | 🤖 自动 | `console market` 随时看 |

**你每天只做三件事**：`console pending` 看待办 → 在 send / book / balance 三个闸门点头。
其余机器自转。碰钱和对外承诺永远经你——这是 §3 安全底线，不会变。

---

## 二、启动（三步）

**1. 装依赖 + 配环境变量**（凭证只走环境变量，绝不进代码/库）
```
pip install openai            # 仅客服 LLM 抽取需要；不接 LLM 可跳过
export SS_WEBHOOK_SECRET=...   # SaleSmartly webhook 签名 secret
export PAYPAL_CLIENT_ID=...    # PayPal 商家（沙箱先验：PAYPAL_ENV=sandbox）
export PAYPAL_SECRET=...
export PAYPAL_CURRENCY=USD     # 海外客户；注意库内 CNY，跨币种要先换 FX
export BROKER_BASE_URL=...     # 客服 LLM（DeepSeek 等，经 broker）；不接可省
export BROKER_TOKEN=...
```

**2. 先灌真价格库**（否则报价会诚实报缺、不编价）
```
python3 -m company.systems.import_data 你的价格表.csv   # 模板见 price_book_template.csv
```

**3. 启动自主运行进程**
```
python3 -m company.autopilot --interval 60             # 前台先看着转
# 一切正常后，跑成常驻：
nohup python3 -m company.autopilot --interval 60 > autopilot.log 2>&1 &
```
（想只看一轮不常驻：`python3 -m company.autopilot --once`。）

---

## 三、让 SaleSmartly 能打进来

autopilot 内置的 webhook 服务默认监听 `127.0.0.1:8899/salesmartly/webhook`。
SaleSmartly 在公网，要能打到你：
- **临时/验证**：内网穿透（cloudflared / ngrok）把本地 8899 暴露成一个 HTTPS 域名。
- **正式**：部署到一台有公网域名的机器，前面挂反代 + TLS（Nginx/Caddy），转发到 8899。

拿到公网 HTTPS 地址后，去 SaleSmartly 后台（Max/企业版）把「新消息」webhook 指到
`https://<你的域名>/salesmartly/webhook`，并把签名 secret 设进 `SS_WEBHOOK_SECRET`。

> 本机没配公网/穿透时，先用 `--no-verify` + `--no-webhook` 跑对账循环，或用
> `demo_full_run` 看整链路彩排。

---

## 四、日常操作（你的三个闸门）
```
python3 -m company.console pending            # 看所有待办卡在哪个闸门
python3 -m company.console send <quote_id>    # 闸门1：批准把报价发给客户
python3 -m company.console book <order_id>    # 闸门3：批准向供应商正式下单
python3 -m company.console balance <order_id> # 闸门4：客户落地后，确认收尾款
python3 -m company.console market             # 转化漏斗 + 投放优化建议
python3 -m company.console requote <quote_id> --markup 0.25   # 二次报价（低价引流后重报）
```

---

## 五、健康检查 / 出错时
- 看日志：`tail -f autopilot.log`（每轮会打印自动报价数、对账推进数、待人审数、GMV）。
- 报价总「incomplete_pricebook」→ 价格库没这条资源，回到第二步补 CSV。
- PayPal 对账不动 → 检查 `PAYPAL_*` 环境变量、`PAYPAL_ENV`（沙箱/生产）。
- webhook 收不到 → 确认公网地址可达、`SS_WEBHOOK_SECRET` 与后台一致（用
  `salesmartly.debug_signature` 比对「收到 vs 算出」锁定签名参与项）。
- 全套自检：`python3 -m pytest company/ -q`（应 102 绿）。

---

**一句话**：灌真价格 → 配三组凭证 → `nohup autopilot` 常驻 → 每天 `console pending`
点三个闸门。机器负责跑，你负责在碰钱和对外承诺处拍板。
