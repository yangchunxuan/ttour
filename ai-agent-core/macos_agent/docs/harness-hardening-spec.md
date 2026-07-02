# 规格书：macos_agent harness 加固（把代码库打造成 agent-friendly 环境）

> 交付对象：实现者（Codex / Gemini / 人）。验收人：Claude（按 §7 逐条审 + 真跑工具）。
> 依据：OpenAI《Harness engineering: leveraging Codex in an agent-first world》。核心结论——**让 agent 高效产出的瓶颈通常是"环境规格不足"，不是模型能力**；工程师的活从写代码转向**设计让 agent 能可靠工作的环境**。本 spec 把 `macos_agent` 这个新代码库按该文三条原则加固，好让日后用 agent（Codex/Claude/Gemini）迭代它时**又快又不塌地基**。
> **先读 §0 铁律。这份 spec 只加"治理层"，绝不改 brain/macos/broker 的运行行为（A8 大脑零回归是硬约束）。**

---

## 0. 铁律（给实现者，违反即退回）
1. **不碰运行行为**：`brain/`、`macos/`、`broker.py`、`run_agent.py` 的逻辑一行都不许改。本 spec 只**新增**文档、lint 工具、CI/钩子、GC 工具。改这些文件唯一允许的情形：给它补模块 docstring（若缺），仅此。
2. **零重依赖**：所有新工具用 **Python 标准库 + AST**，最多加 `pyyaml`。不得引入 flake8/ruff/mypy 等重框架（原因见 §2 说明：我们要的是**领域专属不变量**，不是通用风格）。不得联网。
3. **每个工具自包含、可单跑**：`python3 tools/<x>.py` 直接能跑，不依赖别的工具。
4. **报错必须可操作**：每条 lint 失败的输出格式固定为
   `[INVARIANT <id>] <file>:<line> — <问题一句话> → 修复：<具体怎么改>`。
   通用报错（"格式不对"）不合格。
5. **诚实记分牌**：首次跑 lint 一定有存量违规（已知：`brain/agent.py` 690 行超 500 上限）。**不许为了绿而假装**——违规要么修，要么进 §3 的 `known-exceptions` 台账并写明理由。假绿一票否决。

---

## 1. 交付物总览
```
macos_agent/
├─ AGENTS.md                      # 新：~100 行"目录"，agent 进仓第一眼看的地图
├─ docs/
│  ├─ harness-hardening-spec.md   # 本文件
│  ├─ macos-desktop-agent-spec.md # 已有：总设计 spec
│  ├─ utm-snapshot-restore-spec.md# 已有
│  ├─ INVARIANTS.md               # 新：不变量清单（人读版，lint 的真相源之一）
│  └─ PRINCIPLES.md               # 新：黄金原则台账（GC 对照它）
├─ tools/
│  ├─ lint_invariants.py          # 新：支柱 B，机械校验架构不变量
│  ├─ lint_knowledge.py           # 新：支柱 A，校验知识库不腐烂
│  ├─ gc_scan.py                  # 新：支柱 C，扫偏差/质量降级出分级报告
│  └─ known_exceptions.yaml       # 新：存量违规台账（带理由 + 到期复查）
├─ check.sh                       # 新：一键跑 lint_invariants + lint_knowledge + pytest
├─ .githooks/pre-commit           # 新：提交前跑 check.sh（快检子集）
└─ .github/workflows/harness.yml  # 新：CI 跑 check.sh
```

---

## 2. 支柱 B —— 不变量 lint + 报错内嵌修复指引（先做这个，最硬核）

> 为什么先做 B：文章说"把架构规则编码成机械 linter，别指望 agent 遵守叙述性指南"。macos_agent 的架构约束现在**散在各文件的 docstring 和总 spec 的 §7A 里**——agent 改代码时看不全、会漂移。把它们变成一跑就报的 lint，是回报最高的一步。

### 2.1 `tools/lint_invariants.py` 要求
- 纯 stdlib + `ast`。入口 `python3 tools/lint_invariants.py`（在 macos_agent 根目录跑）。
- 每条检查是一个函数，返回 0..N 条 `Finding(invariant_id, file, line, problem, fix)`。
- 退出码：有任何非豁免违规 → 非 0；全过或全豁免 → 0。
- 读 `tools/known_exceptions.yaml`：命中的 (invariant_id, file) 降级为 warning、不致失败，但**打印**"⚠️ 豁免中"。
- 输出既给人看（§0.4 格式）也给机器：`--json` 输出结构化列表。

### 2.2 不变量清单（基于真实代码，逐条实现）
> 每条给出：判据 + 该报的修复指引。`INVARIANTS.md` 是人读版，本表是实现依据。

| id | 判据（怎么查） | 违反时的修复指引 |
|---|---|---|
| **INV-01 文件行数** | 每个 `.py`（不含 tests/、.venv/）行数 ≤ 500 | "拆分该文件为多个 <400 行模块，或若确因外部约束（如 A8 大脑零回归）不能拆，登记进 known_exceptions.yaml 并写理由" |
| **INV-02 brain 不拉 Playwright** | AST 扫 `brain/__init__.py` 及 `brain/*.py` 顶层 import，不得出现 `playwright`、`agent.dom`、`agent.actions`、`agent.browser`（懒加载在函数体内的 try/except 允许） | "把该 import 移进函数体的 try/except，macOS 路径强制注入实现（见总 spec §7A-C1/agent.py 懒加载）" |
| **INV-03 A8 可导入** | 子进程 `python3 -c "import brain.agent, brain.llm, brain.utils"` 在**不装 playwright** 的解释器下退出码 0 | "顶层出现了会拉起 Playwright 的 import；改懒加载（§7A）" |
| **INV-04 动作空间单一真相源** | `macos.prompts.ACTION_SPEC` 的键集合 == `macos.actions._HANDLERS` 的键 ∪ `{wait, done}`（inert） | "动作名在 prompts 和 actions 里对不上：补齐缺的 handler 或从 ACTION_SPEC 删掉；两边必须一致（总 spec §6）" |
| **INV-05 注入前言同源** | `macos.prompts.get_system_prompt()` 的返回含 `INJECTION_PREAMBLE`；`brain.utils` 源码含 `_EXTRACT_INJECTION_PREAMBLE` 且被用在 extract 的 system prompt | "主 planner 和 extract 提示词必须都带数据/指令隔离前言（A11/§2A.5）；补上缺的那半边" |
| **INV-06 broker constant-time** | `broker.py` 里对 bearer token 的比较用 `hmac.compare_digest`，源码不得出现 `auth == ` / `== expected` 这类裸比较 token | "token 比较改 `hmac.compare_digest`，防计时侧信道（§2A.3.2）" |
| **INV-07 无硬编码密钥** | 全仓（含 scripts/）grep 不得出现形如 `sk-[A-Za-z0-9]{20,}` 的字面量 | "把 key 从代码里删掉；真 key 只经环境变量进宿主 broker，绝不进代码/VM（A7）" |
| **INV-08 guard 精确白名单 + 无旁路** | `macos/guard.py` 的机型判定不得用 `in`/子串匹配含 "Virtual"（必须精确 ==/白名单集合）；全仓不得出现 `--force` 绕过守卫的分支 | "机型用精确白名单集合判定，不要模糊子串；守卫无 --force 旁路（§2A.1）" |
| **INV-09 extract 走 broker** | AST 查 `brain/llm.py` 的 `Planner.extract` 调用 `extract_information_json` 时传了 `base_url=` 和 `api_key=` | "extract 必须把 base_url/api_key 穿透进 utils，否则绕过 broker 直打 api.deepseek.com（§7A-C3）" |
| **INV-10 rm -rf 守卫** | `scripts/*.sh` 里每个 `rm -rf "$VAR"` 前，同函数/同块内有对该变量的守卫（`[[ "$VAR" == *.utm ]]` 或非空检查） | "rm -rf 目标前加路径守卫，防空变量/误删（utm_restore 的教训）" |
| **INV-11 DomState 契约** | AST 确认 `macos.observe.MacDomState` 有属性/方法 `url,title,page_text,elements,get,to_prompt`；`MacElement` 有 `index,text,attributes,render` | "补齐 agent.py 循环消费的字段，缺一个循环就崩（§5）" |
| **INV-12 模块 docstring** | 每个 `.py`（不含空 `__init__`）首个语句是字符串 docstring | "给该模块加一句 docstring 说明它是干嘛的（agent 靠它建立上下文）" |

> **实现提示**：INV-04/05/09/11 建议 import 模块后内省（`ACTION_SPEC.keys()` 等），比纯 AST 稳；但 import 要在无 playwright 也能成功的前提下（brain 已满足）。INV-02/06/07/08/10/12 用 AST/grep 即可。

### 2.3 报错格式（硬性）
```
[INVARIANT INV-01] brain/agent.py:1 — 文件 690 行，超过 500 行上限 → 修复：拆成 <400 行模块，或登记 known_exceptions.yaml 并写理由
```

### 2.4 known_exceptions.yaml（诚实台账）
```yaml
# 每条：为什么豁免 + 到期复查日期。GC（支柱 C）会盯着到期项。
- invariant: INV-01
  file: brain/agent.py
  reason: "从网页版逐行复制，A8 要求大脑零回归，暂不拆；待抽出 loop_guards 已用的辅助后再评估"
  review_by: "2026-09-01"
```

---

## 3. 支柱 A —— 知识库防腐 + doc-gardening

> 文章：AGENTS.md 当"目录"不当"百科"（~100 行），真知识分层放 docs/，**CI 校验知识库不腐烂**，doc-gardening agent 扫陈旧。原则："agent 运行时拿不到的东西=不存在。"

### 3.1 `AGENTS.md`（≤ 120 行，目录性质）
必含：
- 一句话项目是什么（macOS VM 桌面代理）。
- **仓库地图**：每个顶层目录/关键文件一行说明（brain/=复用大脑，macos/=眼睛和手，broker.py=宿主密钥中转，等）。
- **改代码前必读**：指向 `docs/macos-desktop-agent-spec.md` 的 §7A（改大脑的坑）、`docs/INVARIANTS.md`、`check.sh`。
- **铁律速查**（3~5 条）：不改大脑运行行为、key 绝不进代码/VM、动作空间单一真相源、守卫 fail-closed、改前跑 `./check.sh`。
- 不塞细节——细节都在 docs/。**超过 120 行即 lint 失败**（见 INV-KB-01）。

### 3.2 `docs/INVARIANTS.md`（人读版不变量清单）
把 §2.2 的 12 条不变量用人话列出来（id、为什么重要、lint 会怎么报）。它是 `lint_invariants.py` 的"文档镜像"——两者必须对得上（见 INV-KB-03）。

### 3.3 `tools/lint_knowledge.py` —— 知识库校验
| id | 判据 | 修复指引 |
|---|---|---|
| **INV-KB-01 AGENTS.md 瘦身** | `AGENTS.md` ≤ 120 行 | "AGENTS.md 是目录不是百科，把细节挪进 docs/" |
| **INV-KB-02 链接不断** | AGENTS.md 和 docs/*.md 里引用的相对路径文件都存在 | "补上缺失文件或修链接" |
| **INV-KB-03 不变量文档同步** | `docs/INVARIANTS.md` 里出现的每个 `INV-\d+` 都在 `lint_invariants.py` 里有实现，反之亦然 | "文档和 lint 的不变量清单漂移了：补齐缺的一边" |
| **INV-KB-04 §编号可解析** | docs 里引用的 `§7A-C3` 这类编号，在 `macos-desktop-agent-spec.md` 里能找到锚点 | "引用了不存在的 §编号，修正或补章节" |

### 3.4 doc-gardening（`gc_scan.py` 的一个子命令，见支柱 C）
扫"代码里有、文档没记"的漂移，报告（不自动改）：
- 新的 `launch_app` 白名单 app 没写进 SETUP.md 的 env 速查表。
- 新的 `MACOS_AGENT_*` / `BROKER_*` 环境变量没进 SETUP.md 速查表。
- `ACTION_SPEC` 里的动作没在 AGENTS.md/SETUP 提及。
- known_exceptions.yaml 里 `review_by` 已过期的条目。

---

## 4. 支柱 C —— 黄金原则 GC 自动重构

> 文章：Codex 会复制仓库里已有的模式（包括不好的）→ 必然 drift。解法=定期 GC agent 扫偏差、给质量打分、对**低风险**项自动开重构 PR。
> **纪律（这条很重要）**：静态分析是**线索不是判决**——只对机械安全的项自动改，高风险一律只报不改、留给人拍板。

### 4.1 `docs/PRINCIPLES.md`（黄金原则台账）
列 macos_agent 的"黄金原则"（GC 对照它扫偏差）。至少：
- 大脑运行行为零回归（改 brain 必须 `import brain.*` 仍绿 + 不改语义）。
- 动作空间单一真相源。
- key 绝不进代码/VM。
- 每个模块 <400 行、有 docstring。
- 守卫 fail-closed、无旁路。
- 提示词的注入前言主/extract 同源。

### 4.2 `tools/gc_scan.py` 要求
- `python3 tools/gc_scan.py` → 输出 markdown 分级报告（🟥高风险只报 / 🟨中 / 🟩机械安全可自动修）。
- `--fix` 只对 🟩 项自动改（当前仅：补缺失的模块 docstring 占位、`known_exceptions.yaml` 过期项标记）。**绝不自动改 brain/macos/broker 的逻辑**。
- 扫的偏差项：
  - 超 500 行且不在豁免台账的文件（🟥 拆分需人判断）。
  - 缺 docstring（🟩 可自动补占位）。
  - 疑似"重复实现"：两个函数体 AST 结构高度相似且不在同文件（🟨 线索，人看——文章说 Codex 会复制已有模式、导致 drift）。
  - known_exceptions 过期（🟨 提醒复查）。
  - doc-gardening 的漂移项（§3.4，🟨）。
- 报告尾部：一句"本报告是线索不是判决，🟥/🟨 需人拍板"。

---

## 5. `check.sh` / 钩子 / CI

### 5.1 `check.sh`（一键）
```
#!/bin/bash
set -uo pipefail
python3 tools/lint_invariants.py || FAIL=1
python3 tools/lint_knowledge.py || FAIL=1
python3 -m pytest tests/ -q || FAIL=1
exit ${FAIL:-0}
```
（诚实：首跑因 agent.py 会红，直到把它登记进 known_exceptions.yaml。）

### 5.2 `.githooks/pre-commit`
跑 `lint_invariants.py`（快，秒级）。慢的（pytest）留给 CI。附一行安装说明：`git config core.hooksPath .githooks`。

### 5.3 `.github/workflows/harness.yml`
在 push/PR 上 `bash check.sh`。macOS 专属测试（真 AX）在 CI 跑不了，所以 CI 只跑 lint + 不需 pyobjc 的单测（现有 26 测已满足：不需真 VM/pyobjc/网络）。

---

## 6. 已知存量违规（诚实先说，别假装绿）
首次跑 `lint_invariants.py` 必然报：
- **INV-01 `brain/agent.py` 690 行** —— 从网页版复制、A8 零回归暂不拆。处理：登记 `known_exceptions.yaml`（理由 + `review_by`），不是假装没有。
- 可能 INV-12：个别 `__init__.py` 无 docstring —— 直接补。

实现者跑完一遍 lint，把真实存量违规如实填进台账，**不许改判据来凑绿**。

---

## 7. 验收标准（Claude 逐条真跑）
| # | 标准 | 判法 |
|---|---|---|
| V1 | AGENTS.md 是目录 | ≤120 行，仓库地图 + 铁律速查 + 指向 docs/；INV-KB-01 过 |
| V2 | 不变量 lint 真抓存量违规 | `lint_invariants.py` 跑起来，agent.py 500 行违规被抓、报错带修复指引；豁免机制生效 |
| V3 | 每条不变量有反例测试 | 每个 INV-xx 配一个单测：注入一个违规样本 → lint 报对应 id（放 tools/tests 或 tests/）|
| V4 | 报错可操作 | 随机挑 3 条违规，报错都符合 `[INVARIANT id] file:line — 问题 → 修复：…` 格式 |
| V5 | 知识库校验生效 | 故意断一个 docs 链接 / 加一个 lint 有但 INVARIANTS.md 没有的不变量 → lint_knowledge 报错 |
| V6 | GC 分级不误伤 | `gc_scan.py` 输出🟥🟨🟩报告；`--fix` 只动 docstring 占位，`git diff` 确认 brain/macos/broker 逻辑零改动 |
| V7 | doc-gardening 抓漂移 | 临时给 actions 加个白名单 app 不写进 SETUP → gc_scan 报"文档漂移" |
| V8 | 一键 + CI | `./check.sh` 一条命令跑全套；CI workflow 语法有效、只跑离线可跑的部分 |
| V9 | 零重依赖零联网 | 所有工具 `python3 tools/x.py` 纯 stdlib（+可选 pyyaml）能跑，无网络 |
| V10 | 运行行为零回归 | 现有 26 单测仍全绿；`git diff` 显示 brain/macos/broker.py 逻辑未变（只可能加了 docstring）|

## 8. 未来支柱（本次不做，登记备忘）
- **可观测自验**（文章的第 4 招）：把桌面代理每步的 observe/transcript/截图接成"agent 自己能查、自己验自己的活"的回路——直击"假成功"。等本三支柱落地、有真跑数据后再立项。
