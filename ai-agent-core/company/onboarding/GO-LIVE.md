# 上线清单：把系统从「示例数据」切到「真实运营」

引擎、护栏、全链路、两个真外部适配器（SaleSmartly 进客户 / PayPal 收款）都已建好、
测过（82 测绿）。现在只差**你的真实世界输入**。下面每一项都是「你给 → 我立刻接上真跑」。
不用一次给全，**给一项，那条就从占位变真**。

---

## ① 真价格库 —— 解锁「真报价」（最高优先，最容易给）

计调 agent 现在用示例价。给我真价，报价立刻变真。

1. 打开 `company/onboarding/price_book_template.csv`，删掉示例行，填你的真实价格
   （一行一个资源价：供应商、城市、类型、成本元、日期段；类型只能是
   `hotel/car/guide/ticket/dmc/transport`）。哪怕**先只填一个城市**也行。
2. 一条命令灌进去：
   ```
   python3 -m company.systems.import_data 你的价格表.csv
   ```
   格式不对的行会被跳过并报出来（我不编价）。灌完 `console intake` 出的就是真报价。

## ② 退改分档数字 —— 解锁「真退款」

你自述"需要完善：距离日期相差多少天退多少"。这就是那张表，**只有你能定数字**：
- 填 `company/onboarding/refund_tiers_template.csv`（酒店按天数分档；机票/高铁/景区/
  导游/司机按各自政策）。给我数字，退款算法（`refund.refund_pct`，现未配置返回 None）立刻变真。

## ③ SaleSmartly 上线 —— 真客户进来

代码就绪（入站 webhook 照官方契约实现）。你在 SaleSmartly 后台（Max/企业版套餐）：
1. 把「新消息」webhook 指到本服务：`https://<你的公网域名>/salesmartly/webhook`
   （本机起服务：`python3 -m company.systems.salesmartly serve`；公网可达需内网穿透/部署）
2. 把该 webhook 的**签名 secret** 给我 → 设环境变量 `SS_WEBHOOK_SECRET`
3.（要自动回客户才需要）Max 套餐 **API token** + 你 apifox 文档里的出站端点路径

## ④ PayPal 收款上线 —— 真收定金/尾款

代码就绪（Invoicing v2，只开发票+查状态，绝不划钱）。给我：
- `PAYPAL_CLIENT_ID` / `PAYPAL_SECRET`（商家账号，**沙箱可先验**：`PAYPAL_ENV=sandbox`）
- 结算币种 `PAYPAL_CURRENCY`（海外客户一般 USD；注意库内金额是 CNY 分，跨币种要先换 FX）

## ⑤ 供应商下单方式 —— 我接下单动作

现在下单是占位。告诉我你的供应商**怎么收单**（邮件 / 微信 / 门户 API？），我照那个接。

## ⑥（可选）支付宝 / 广告平台

- 支付宝：有公开 API，但需中国商户号 + RSA2 签名；要用就给我商户信息，我照官方 API 建。
  （你客户在海外 Facebook/Ins，PayPal 其实更对口，支付宝可后置。）
- 广告平台：投放优化建议已能产出（🟢加投/🔴暂停/调仓）；真去平台改预算需你的广告账号。

---

**安全底线（不会变）**：碰钱/对客户或供应商承诺，永远人审；凭证只走环境变量、绝不进代码。
**给我 ① 的一张真价格表，是见效最快的一步。**
