# ai-agent-core — 从 DeepSeek_Scraper 抽取的可复用「大脑」+ 桌面代理规格

这一整个目录是从另一个项目 `DeepSeek_Scraper`（一个 Playwright 网页抓取 + ReAct 桌面代理实验）里，**挑出对旅游公司自动化 / Mac 上 computer use 真正可复用的部分**搬过来的。不是完整项目，是精选。

## 里面是什么

### `brain/` — 模型无关的 ReAct「大脑」（纯 Python，跨平台）
把「读观察 → 让 LLM 决策 → 执行动作 → 再观察」这个循环和 LLM 调用抽象出来。**已经重构成可注入、模型无关**——观察器/执行器/提示词/健康检查都能从外面传进去，所以接 Mac 的 computer use（或换 OpenAI / Gemini API）时，这套循环逻辑不用重写。

| 文件 | 是什么 | 复用价值 |
|---|---|---|
| `agent.py` | ReAct 控制循环（循环检测、幻觉编号守卫、恢复计数、auto-extract、守卫拒绝连击等打磨过的状态机）。`ReActAgent.__init__` 收 `observe_fn/execute_fn/health_fn/dismiss_popups_fn` 四个可注入函数 | ★★★ 核心，别重写 |
| `loop_guards.py` | 循环用的纯函数（动作签名、页面指纹、各种守卫）。注意：`_blocked_go_to_reason`/`_blocked_type_reason` 是**网页专属**，桌面/computer use 路径要跳过或改掉 | ★★★ |
| `llm.py` | `Planner`——把 GOAL+观察+历史组织成消息调 LLM，再把脏输出修成合法动作。构造函数收 `model/base_url/api_key/prompts_module`，**换模型只改这里** | ★★★ |
| `utils.py` | `extract_information_json`——把大白话/邮件/PDF 文本抽成结构化 JSON。**旅游业务里最直接能用的一块**（解析客户需求、供应商报价） | ★★★ |
| `health.py` | 页面健康分类（是否可用/被拦/需登录）。纯 duck-typed，可直接复用 | ★★ |
| `prompts.py` | 网页版 ReAct 系统提示词 + 动作空间定义（`ACTION_SPEC` 单一真相源）。**这是网页版的，仅作参考**——Mac computer use 要写自己的一份 | ★（参考） |
| `__init__.py` | 让 brain 成为包。注意它 `from .llm import Planner`，见下方“跑之前必改” | — |

### `specs/` — 两份经过对抗审查的桌面代理规格书
| 文件 | 是什么 |
|---|---|
| `windows-sandbox-agent-spec.md` | Windows Sandbox 里跑桌面代理的规格（模式三）。**Windows 专属，Mac 上主要作参考**，看它怎么定义验收表/守卫/安全边界 |
| `macos-vm-agent-spec.md` | **macOS VM 里跑桌面代理的规格（模式四）——你在 Mac 上要用的就是这份。** 经两轮审查（技术+安全）修订，含验收表 A1–A11、里程碑、以及所有已知坑（快照、AX 权限、broker、复用大脑的导入陷阱等） |

## ⚠️ 跑之前必改（否则 import 就崩 / extract 静默失败）
这些文件是从 Windows 项目**原样复制**的，直接在 Mac 上 import 会出问题。**具体改法在 `specs/macos-vm-agent-spec.md` 的 §7A 里写得很细**，摘要：
1. `agent.py` 顶部 `from agent.dom / .actions / .health import …` 会连带拉 Playwright（Mac 上没装）→ 崩。改成懒加载或可选，computer use 路径强制注入自己的函数。
2. `utils.py` / `llm.py` 里 base_url、api_key 写死了 DeepSeek。换成你的 broker 或 OpenAI/Gemini 端点。特别注意 `utils.extract_information_json` 自建 client、URL 是字符串字面量，**光改 Planner 不够，这个也得改**。
3. `_blocked_type_reason` 网页守卫在桌面会误拦，要 no-op。
4. `brain/__init__.py` 要确保不 re-import 任何 Playwright 相关模块。

## 提示
- 换 OpenAI / Gemini：它们的 SDK 大多兼容 OpenAI 格式，`Planner` 和 `utils` 的 `base_url`/`api_key` 指过去即可；不兼容的（如 Gemini 原生）要写个薄适配层。
- `brain/` 是纯 Python，Mac 上直接能跑（改完上面几条之后）。
- Playwright 网页抓取那套、Windows 桌面 `desktop/` 代码**没搬**（Mac 用不上）。需要网页抓取时，`llm.py`+`utils.py` 的 LLM 抽取这层仍可复用，只是「取网页文本」那步在 Mac 上换 computer use 或别的方式。
