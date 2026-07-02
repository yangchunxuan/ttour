# 规格书（给 Gemini）：UTM「穷人快照」还原脚本 `scripts/utm_restore.sh`

> 交付对象：Gemini（写 bash）。验收人：Claude（会逐条按 §验收 审核 + 真实 `bash -n`/dry-run）。
> 目标产物：一个文件 `macos_agent/scripts/utm_restore.sh`。**只写这一个 bash 脚本，别动别的文件。**
> 本 spec 里标了「✅ 已核实」的技术事实是经过多源交叉验证的**硬约束**，照做，别自己发明命令/路径。标了「⚠️ 需你确认」的，实现时用 `utmctl --help` 或官方文档再核一遍。

---

## 0. 一句话目标
在**宿主 Mac**（Apple Silicon）上，用 APFS 写时复制克隆（`cp -c`）把一台 **UTM 的 macOS 虚拟机**秒级还原到"黄金副本"（干净基线）。这是免费 UTM 方案下对上游 spec `docs/macos-desktop-agent-spec.md` §2A.4「焚 = 关机 + 从磁盘副本还原」的兑现——UTM 的 macOS guest **没有运行中快照**，只能靠这招。

## 1. 背景（为什么要这脚本）
- 主项目：一个 AI 代理跑在 macOS 虚拟机的 guest 桌面里。代理若读到恶意内容被注入，VM 可能被持久感染。
- §2A.4 要求：**每次真跑前必须能证明"从干净状态启动"**。免费 UTM 无快照，所以每次跑前把整台 VM 还原到"装好系统+授权+种好身份、之后再没动过"的黄金副本。
- 为什么快：黄金副本和工作副本都在同一个 APFS 卷上时，`cp -c` 是**克隆**（copy-on-write），几十 GB 的 `.utm` 包克隆只要几秒、初始零额外占用。所以"关机+还原"不是分钟级，是秒级。

---

## 2. ✅ 已核实的技术事实（硬约束，照抄）

### 2.1 utmctl（UTM 的命令行控制工具）
- **二进制路径固定**：`/Applications/UTM.app/Contents/MacOS/utmctl`。**默认不在 PATH 上**，必须用全路径调。
- ⚠️ 它是 UTM 的 AppleScript/ScriptingBridge 封装：**需要 UTM.app 在跑 + 有登录中的 GUI 会话**；调它时若 UTM 没开，它会拉起 UTM。（→ 见 §2.3 的顺序矛盾。）
- 子命令（✅ 已核实语法）：
  - `utmctl list` —— 列出所有 VM（名字 + UUID + 状态）。
  - `utmctl status <名字或UUID>` —— 打印状态。**状态是固定枚举**：`stopped` / `starting` / `started` / `stopping` / `pausing` / `paused` / `resuming`。你只需关心 `started` 和 `stopped`。
  - `utmctl start <名字或UUID>` —— 开机。
  - `utmctl stop <名字或UUID>` —— 停机。**三种力度**：
    - `utmctl stop --request <名字>` = 优雅关机（相当于请客系统自己关）。
    - `utmctl stop --kill <名字>` = **强杀 VM 进程**。
    - `utmctl stop <名字>`（不带 flag）= 默认停止。
  - ✅ **已核实的坑**：**macOS guest 用 `stop --request` 经常关不干净**（UTM 已知问题）。所以对 macOS guest，停机用 `stop --kill` 更可靠。
  - ✅ **已核实的坑**：`stop` 是**异步**的——命令返回时 VM 可能还没真停。**必须轮询 `utmctl status` 直到 `stopped`，带超时。**
- VM 既可用**名字**也可用 **UUID** 指定（✅ 已核实）。
- ⚠️ 别用 `utmctl clone`：它复制的配置**连 MAC/UUID 一起复制**（同一身份）。我们不需要它——我们在**文件层**用 `cp -c` 克隆整个 `.utm` 包，本来就是要"同一台 VM 回到旧状态"。

### 2.2 UTM 的 .utm 包
- ✅ **App Store 版**默认存放路径（沙箱）：`~/Library/Containers/com.utmapp.UTM/Data/Documents/<VM名>.utm`。
- ⚠️ **直接下载版**（GitHub 版）路径可能不同（常见 `~/Library/Containers/com.utmapp.UTM/Data/Documents/` 也可能是用户自选目录）。→ **路径必须做成可配置**（环境变量），不写死。
- ✅ `.utm` 是一个**目录包**（package）：里面有 `config.plist` + `Data/` 目录（含几十 GB 的磁盘镜像）。macOS guest 磁盘**默认 64GB、稀疏**（实占按真实写入量）。
- ✅ **最要命的坑**：**UTM 在启动时把 config.plist 读进内存**。你在 UTM 开着的时候替换/删除包里的文件，**UTM 看不到，要退出并重开 UTM 才生效**。失败模式是"改动被忽略/状态陈旧"，不是文件损坏——但**还原不重启 UTM 就不会生效**。
  - → 因此还原流程**必须**：停 VM → **完全退出 UTM.app** → 替换文件 → 重开 UTM。

### 2.3 顺序矛盾（务必处理好）
utmctl 要 UTM 开着才能用；但替换 `.utm` 文件又要 UTM **退出**。所以正确顺序是：
1. （UTM 开着）`utmctl stop --kill <VM>`，轮询 `status` 到 `stopped`。
2. **退出 UTM.app**（`osascript -e 'quit app "UTM"'`；给几秒；还没退就 `killall UTM`；用 `pgrep -x UTM` 确认真没进程了）。
3. 替换文件（见 §2.4）。
4. 重开 UTM（`open -a UTM`），给它两三秒起来，再 `utmctl start <VM>`（可选，见 `--start`）。

### 2.4 APFS 克隆（cp -c）
- ✅ `cp -c` 请求 clonefile（APFS 写时复制）。对目录包用 `cp -c -R`。
- ✅ **前提**：源和目标必须在**同一个 APFS 卷**上，`cp -c` 才是克隆；跨卷会退化成真深拷贝（慢但仍正确）。→ 脚本要**检测是否同卷**并在不同卷时**大声警告**（不阻断）。
  - 同卷检测：比较两个路径的挂载设备。可用 `stat -f '%d' <path>`（同一 device id = 同卷），或 `df` 取挂载点比较。用父目录判断（目标可能还不存在）。
- ✅ **ENOSPC 警示**（写进注释即可）：克隆不预分配空间，日后卷快满时"改一个已存在文件"也可能因写时复制分裂失败报 ENOSPC。所以还原前最好留足空间。
- ✅ 编辑克隆出来的文件**不影响**原件（copy-on-write 各自私有）——所以工作副本跑脏了，黄金副本纹丝不动。

---

## 3. 脚本要求（`scripts/utm_restore.sh`）

### 3.1 通用
- 头部 `#!/bin/bash` + `set -euo pipefail`。
- 顶部一大段注释：说清原理（§1/§2）、一次性准备步骤、每次用法、已知坑（退 UTM、同卷、ENOSPC）。
- **fail-closed**：任何前置条件不满足（黄金副本不存在、VM 停不下来、UTM 退不掉）→ 打印清楚原因 + 非零退出，**绝不**在不确定状态下替换文件或起 VM。
- 所有路径/名字**可配置**（环境变量 + 合理默认）：
  - `UTM_DOCS`（.utm 所在目录，默认 `~/Library/Containers/com.utmapp.UTM/Data/Documents`）
  - `UTM_GOLDEN`（黄金副本 `.utm` 的完整路径，默认 `$HOME/UTM-golden/<VM名>.utm`）
  - `UTMCTL`（默认 `/Applications/UTM.app/Contents/MacOS/utmctl`）
- **不留任何宿主后台 watcher/轮询**（上游 spec §2A.4/A9）——脚本干完就退，别 `&` 挂后台、别写 launchd。

### 3.2 命令行接口
```
utm_restore.sh <VM名>              # 还原：把工作副本还原成黄金副本
utm_restore.sh --start <VM名>      # 还原后顺便开机
utm_restore.sh --snapshot <VM名>   # 反向：从当前(已关机)工作副本创建/更新黄金副本
utm_restore.sh -h | --help         # 用法
```
- VM 名必填；缺了打用法 + 非零退出。
- `<VM名>` 用来拼工作副本路径 `$UTM_DOCS/<VM名>.utm`，也传给 utmctl。

### 3.3 还原流程（默认模式，严格按此序）
1. 校验：`$UTM_GOLDEN` 存在且是目录；不存在 → fail-closed 退出，提示先跑 `--snapshot`。
2. 校验：`utmctl` 二进制存在且可执行；否则退出提示装 UTM。
3. 停 VM：`utmctl stop --kill <VM>`（macOS guest）。然后**轮询** `utmctl status <VM>`，直到出现 `stopped` 或超时（如 60s）。超时 → fail-closed 退出（**绝不**在 VM 没停时动文件）。
4. 退 UTM：`osascript -e 'quit app "UTM"'` → sleep 2 → 若 `pgrep -x UTM` 还在，`killall UTM` → 再等，`pgrep` 确认真没了；退不掉 → fail-closed 退出。
5. 同卷检查：比较 `$UTM_GOLDEN` 与 `$UTM_DOCS`（工作副本父目录）是否同一 APFS 卷；不同卷 → 大声 `echo` 警告"将退化为深拷贝、较慢"，但继续。
6. 替换：`rm -rf "$UTM_DOCS/<VM名>.utm"` 然后 `cp -c -R "$UTM_GOLDEN" "$UTM_DOCS/<VM名>.utm"`。
   - 用变量、加引号，防路径空格。
7. 重开 UTM：`open -a UTM`，sleep 2~3。
8. 若 `--start`：`utmctl start <VM>`（可再轮询到 `started`）。否则打印"还原完成，可在 UTM 里手动开机"。
9. 打印一行总结：还原自哪个黄金副本、耗时（可选）、下一步提示（"还原后 guest 内 clean_token 回到未消费态，run_agent 的 §2A.4 硬门会放行"）。

### 3.4 快照模式（`--snapshot`）
1. 校验工作副本 `$UTM_DOCS/<VM名>.utm` 存在。
2. **确认 VM 已关机**：`utmctl status` 必须是 `stopped`；不是 → fail-closed 退出（提示先关机，抓运行中的镜像是脏的）。
3. **退出 UTM**（同 §3.3 步骤 4；抓文件前也得退，避免读到半写状态）。
4. 若 `$UTM_GOLDEN` 已存在，先删（或改名备份，你定，但要提示）。
5. 同卷检查（同上）。
6. `cp -c -R "$UTM_DOCS/<VM名>.utm" "$UTM_GOLDEN"`。
7. 打印"黄金副本已更新：$UTM_GOLDEN"。

---

## 4. 验收标准（Claude 会逐条审）
| # | 标准 | 判法 |
|---|---|---|
| V1 | 语法正确 | `bash -n scripts/utm_restore.sh` 无错；`set -euo pipefail` 在 |
| V2 | 黄金副本缺失 → fail-closed | 还原模式下 `$UTM_GOLDEN` 不存在时非零退出、清楚提示，**不碰工作副本** |
| V3 | 停机确认后才动文件 | 轮询 `utmctl status` 到 `stopped` 有超时；超时则退出、不替换文件 |
| V4 | 替换文件前 UTM 已退出 | 有 `quit app "UTM"` → `killall UTM` 兜底 → `pgrep` 确认；退不掉则退出 |
| V5 | 用 `cp -c -R` 克隆 | 确实用 `cp -c`；有同卷检测 + 跨卷警告（不阻断） |
| V6 | 路径/名字可配置 | `UTM_DOCS`/`UTM_GOLDEN`/`UTMCTL` 走环境变量 + 默认；utmctl 用全路径 |
| V7 | `--snapshot` 反向可用 | 要求 VM 已 `stopped` 才抓；抓前退 UTM |
| V8 | 无宿主后台残留 | 无 `&` 挂后台、无 launchd/watcher；脚本干完即退 |
| V9 | 引号与空格安全 | 所有路径变量加双引号；`rm -rf` 目标是拼好的带引号变量，绝不空/裸 `/` |
| V10 | 注释交代坑 | 注释里写清：退 UTM 才生效、同卷才快、ENOSPC 警示 |

**特别提醒 V9**：`rm -rf` 前务必确认目标变量非空且指向 `.utm`（可加一道 `[[ "$target" == *.utm ]]` 守卫），别让空变量把 `rm -rf ""` 或 `rm -rf /` 打出去。

## 5. 已知风险（写进脚本注释，别假装消除）
- utmctl 依赖 GUI 登录会话；无头/SSH 场景可能不工作（本脚本面向宿主本机交互使用）。
- macOS guest 优雅关机不可靠 → 用 `--kill`；但强杀有极小概率留下 guest 文件系统未净关（还原会覆盖掉，可接受）。
- 跨卷时 `cp -c` 退化为深拷贝，慢（几十 GB 分钟级）但正确。
- 黄金副本必须是"授权之后"抓的（TCC 授权存在 guest 里）——这是**主 SETUP 流程**的责任，本脚本不负责，但注释里提一句。
