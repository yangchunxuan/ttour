# macOS VM 桌面代理 — 搭建与运行

按 `docs/macos-desktop-agent-spec.md`（模式四 v2）实现。这份 SETUP 是"怎么把它跑起来"的操作手册；安全模型的完整交代（含四条软墙）读 spec §2A。

> **本机现状（2026-07）**：开发机是 Apple Silicon `Mac17,3`（M5）。代理**拒绝**在真 Mac 上执行任何改状态动作——这是设计（§2A.1）。真跑必须在选定的 macOS 虚拟机里。

---

## M0 — 阻塞门（先做，定不下来别往下走 / spec §9）

**选定唯一一个 VM 后端，并实测它能否"干净回滚"。** 这一步决定两件事：
- §2A.1 守卫的机型白名单（改 `MACOS_AGENT_VM_MODELS`）。
- §2A.4 的一次性能力。

| 后端 | 回滚 | 代价 |
|---|---|---|
| **Parallels**（付费）| macOS guest 支持快照 = 真"关掉即焚" | 花钱，最省心，唯一干净兑现 A10 |
| **UTM**（免费，Apple Virtualization 后端）| 无 macOS 运行中快照 → 只能"关机 + 从磁盘副本还原" | 免费，还原是分钟级、几十 GB |

Apple Silicon 上 macOS guest **没有运行中快照**（spec §2A.0#1 最软的墙）。若选 UTM，"焚"= 关机后从黄金磁盘副本还原；VM 若长期不还原，guest 一旦被注入就是**持久感染**——此时安全声明须书面降级（见 spec §2A.4）。

拿到机型串的方法：在 VM 里跑 `system_profiler SPHardwareDataType | grep "Model Identifier"`，把结果精确填进 `MACOS_AGENT_VM_MODELS`（Apple Virtualization 常见是 `VirtualMac2,1`，已是默认；Parallels/UTM-QEMU 各有确定串，别用"包含 Virtual"的模糊匹配）。

---

## 一次性搭建（在 VM 内，顺序不能乱 / spec §3）

**关键顺序**：装系统 → 装依赖 → **授权** → 写 VM 身份 → **抓黄金镜像**。TCC 授权存在 guest 的 TCC 库里，镜像必须在授权之后再抓，否则每次还原都从"未授权"重来，而授权不能自动化。

1. 建 macOS VM、装好系统。
2. VM 内装 Python 3.11+ 和依赖：
   ```
   pip install -r requirements-vm.txt
   ```
3. **固定启动程序**：决定"从哪个程序启动代理"——推荐 **Terminal.app**（或打个签名 .app 壳）。授权是授给这个签名二进制，子进程继承。**换 venv/homebrew 的 python3 会作废授权**（spec §3.3）。
4. VM 内授权（系统设置 → 隐私与安全性）：
   - **辅助功能 (Accessibility)** = 观察 AX + 点击/输入的**命脉**，必须给。
   - **屏幕录制 (Screen Recording)** = 仅每步截图审计。缺它代理照样能操作，只是截图坏 → 启动时降级跳过、不中止。
5. 写 VM 身份（信号 B secret + 干净启动 token）：
   ```
   sudo ./scripts/seed_vm_identity.sh
   ```
6. **关机，抓黄金镜像/快照。** 这是干净基线。

---

## 每次真跑

### 宿主 Mac（持钥，broker 不进 VM）
```
export BROKER_UPSTREAM_KEY=sk-...        # 专用、可吊销、低配额的 DeepSeek key
# host-only 网络时再加：
# export BROKER_BIND=192.168.66.1  BROKER_ALLOW_IPS=192.168.66.2
./scripts/run_broker.sh                  # 打印本次运行的 bearer token
```

### VM guest（从黄金镜像还原后）
```
export BROKER_BASE_URL=http://<宿主host-only地址>:8899/v1
export BROKER_TOKEN=<宿主打印的本次 token>    # 这是 broker token，不是真 key
python3 run_agent.py "开 TextEdit 输入 Hello DeepSeek 存到 ~/out/hello.txt" --app TextEdit
```

`run_agent.py` 启动即过两道硬门（不过直接拒绝退出，无 `--force`）：
1. §2A.4 干净启动证明：消费 `~/.macos_agent/clean_token`。还原过 → token 在 → 放行；长期没还原 → token 已被上次消费 → 拒绝。
2. §2A.1 VM 守卫：机型不在白名单 / 信号 B 缺失 / 守卫抛异常 → 拒绝。

审计产物落在 `~/.macos_agent/runs/run_<时间>/`：`transcript.jsonl` + `screenshots/` + `summary.json`。**这些是"攻击者可控数据"（§4A），宿主要主动拉取并清洗，不要用实时共享目录直接打开。**

---

## §4A 隔离清单（在 hypervisor 里逐一关，默认全关"便利集成"）
- 网络：host-only/私有网络，broker 只放行 guest IP，公网由宿主出。
- 共享目录：放代码的挂载设**只读**（防被注入的 guest 改写 run_agent.py/broker.py）。
- **显式关掉**：共享剪贴板（Parallels/VMware 默认开！UTM+SPICE 装 vdagent 也会同步）、拖放、Coherence/共享应用、USB/蓝牙直通、共享打印机/AirDrop、摄像头、麦克风。

---

## 环境变量速查
| 变量 | 侧 | 作用 |
|---|---|---|
| `MACOS_AGENT_VM_MODELS` | VM | 机型白名单（逗号分隔，精确匹配），默认 `VirtualMac2,1` |
| `MACOS_AGENT_VM_SECRET_PATH` | VM | 信号 B secret 路径，默认 `/etc/macos_agent/vm_secret` |
| `MACOS_AGENT_CLEAN_TOKEN` | VM | 干净启动 token 路径，默认 `~/.macos_agent/clean_token` |
| `MACOS_AGENT_ALLOWED_APPS` | VM | launch_app 白名单追加项（逗号分隔） |
| `MACOS_AGENT_OUT` | VM | 审计产物目录，默认 `~/.macos_agent/runs` |
| `MACOS_AGENT_SKIP_CLEAN_TOKEN=1` | VM | 仅受控演示：跳过 §2A.4 硬门（会大声打旗、安全声明降级） |
| `BROKER_UPSTREAM_KEY` | 宿主 | 真 DeepSeek key（专用可吊销低配额） |
| `BROKER_TOKEN` | 两侧 | guest↔broker 共享 bearer token，每次运行轮换 |
| `BROKER_BASE_URL` | VM | 指向宿主 broker 的 `/v1`，如 `http://192.168.66.1:8899/v1` |
| `BROKER_BIND` / `BROKER_PORT` / `BROKER_ALLOW_IPS` | 宿主 | broker 绑定地址/端口/放行源 IP |
| `BROKER_ALLOWED_MODELS` | 宿主 | broker 允许的模型列表，默认 `deepseek-v4-flash,deepseek-v4-pro` |
| `BROKER_MAX_TOKENS_CAP` / `BROKER_UPSTREAM_TIMEOUT` | 宿主 | broker 单请求 token 上限 / 上游超时 |
| `BROKER_RATE_PER_MIN` / `BROKER_RUN_CALL_CAP` / `BROKER_RUN_TOKEN_CAP` | 宿主 | broker 每分钟、每次运行调用和 token 熔断 |
| `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` | 本地开发 | 仅宿主本地调试 fallback；VM 真跑走 `BROKER_*` |
| `DEEPSEEK_MODEL` | 两侧 | 默认 `deepseek-v4-flash` |

`launch_app` 默认白名单：TextEdit、Calculator、Finder、Notes、Preview。额外应用只通过
`MACOS_AGENT_ALLOWED_APPS` 追加；这不是安全边界，只是给模型的 UX 引导。

---

## 测试
```
python3 -m pytest tests/ -q          # 22 passed（不需要真 VM / pyobjc / 网络）
python3 tests/test_macos_actions.py  # 无 pytest 时的兜底 harness
```
覆盖：A1 守卫 fail-closed（含"守卫抛异常→拒绝"）、干净启动 token 一次性、press_key/launch_app 白名单、MacDomState/MacElement 契约、A8 大脑无 Playwright 可导入、Planner 校验/修复、A11 注入前言静态存在性。

真机验收（A2–A7/A9/A10）须在 VM 内按 spec §8 逐条真跑。
```
