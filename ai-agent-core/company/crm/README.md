# crm/leads.jsonl — 全渠道客户线索快照（LLM-ready）

Orient Surprises Travel 的客户线索数据，由 computer-use 只读读取 SaleSmartly 桌面端
（WhatsApp 渠道 + Messenger 渠道）与 YOYO 个人 WhatsApp 后人工归并。**只读采集，未向任何客户发送过消息。**

## 为什么是 JSONL

- **一行一条记录**：LLM/RAG 可按行检索、流式加载，不用解析整个文件；
- **首行 `type:"meta"` 自描述**：来源、口径、渠道漏斗、词表都在数据里，单文件即完整上下文；
- **受控词表**：`stage` / `priority` 用固定枚举，可直接过滤（如"所有 P0/P1 的 needs_action"）。

## 字段

| 字段 | 说明 |
|---|---|
| `type` | `meta`（首行）/ `lead`（客户）/ `group`（客户群）/ `note`（汇总备注） |
| `id` | kebab-case 唯一 ID |
| `name` / `phone` / `location` | **强脱敏**：name=匿名代号（同 id），phone=null（已移除）；location 保留 |
| `pax` / `travel_window` / `interest` / `quote` | 人数 / 出行窗口 / 兴趣与已发方案 / 报价状态 |
| `stage` | `needs_action` `paid` `in_trip` `hot` `customer_pending` `quoted_waiting` `early_lead` `closed_lost` `invalid` |
| `priority` | `P0`（紧急）→ `P3`（低） |
| `action_needed` / `action_owner` | 下一步行动及归属（欠客户的交付都写在这里） |
| `last_activity` | 最近活动日期 |
| `channels` | `salesmartly_wa` `personal_wa` `messenger` `voice_call` `voice_message` `email` 等 |
| `related_to` | 家庭/群组关联（如夫妻分别咨询同一行程） |
| `salesmartly_title` | SaleSmartly 会话标题原文（含运营侧标记：待报价/待联系/超时等） |
| `summary` | 一句话摘要 |

## 隐私与口径

- 本文件**强脱敏**：客户姓名以 `lead-###` 代号表示、手机号完全移除；代号→真实身份对照表在本地 `company/data/anon_map_*.tsv`，完整数据在本地 `company/data/leads_full_*.jsonl`（均已被
  `company/.gitignore` 的 `data/` 规则忽略，**永远不要提交**）。
- Messenger 会话超过 7 天只能发模板消息（Meta 政策）——超期线索需转 WhatsApp/邮件触达。
- 快照时点见首行 `as_of`；Instagram/Facebook 渠道尚有少量会话未纳入。
