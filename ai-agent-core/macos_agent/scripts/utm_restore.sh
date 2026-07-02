#!/bin/bash
set -euo pipefail

# ==============================================================================
# UTM Restore & Snapshot Script (utm_restore.sh)
#
# PURPOSE:
# This script manages APFS copy-on-write clones of a UTM macOS VM. It allows
# restoring a working VM to a clean "golden" baseline in seconds, or snapshotting
# a configured VM as a new golden baseline.
#
# WHY IT WORKS FAST:
# By using `cp -c`, we create APFS clones. It only takes seconds for tens of GBs
# because they share physical disk space (copy-on-write).
#
# KNOWN CAVEATS & WARNINGS:
# 1. UTM MUST RESTART: UTM caches `config.plist` in memory. Modifications to the
#    .utm package while UTM is open are ignored. This script ensures UTM is fully
#    quit before modifying files, and restarted afterwards.
# 2. SAME APFS VOLUME REQUIRED: `cp -c` only clones instantly if both the source
#    and destination are on the same APFS volume. If they cross volumes, it falls
#    back to a deep copy (slow but still correct).
# 3. ENOSPC RISK: Copy-on-write shares space initially but diverges upon writes.
#    Ensure sufficient free disk space, otherwise modifying a shared file later
#    might fail with an ENOSPC error.
# 4. macOS GUEST GRACEFUL SHUTDOWN: `utmctl stop --request` is unreliable for
#    macOS guests, so this script uses `stop --kill`.
# 5. TCC GRANTS: The golden snapshot should be captured *after* granting required
#    permissions (TCC) to apps inside the guest.
# ==============================================================================

# ------------------------------------------------------------------------------
# Configuration (Customizable via Environment Variables)
# ------------------------------------------------------------------------------
UTM_DOCS="${UTM_DOCS:-$HOME/Library/Containers/com.utmapp.UTM/Data/Documents}"
UTM_GOLDEN_DIR="${UTM_GOLDEN_DIR:-$HOME/UTM-golden}"
UTMCTL="${UTMCTL:-/Applications/UTM.app/Contents/MacOS/utmctl}"

# ------------------------------------------------------------------------------
# Usage & Argument Parsing
# ------------------------------------------------------------------------------
usage() {
    # 参数 $1 = 退出码（默认 1）。--help 应以 0 退出，错误用法以非 0 退出。
    cat <<EOF
Usage:
  $0 <VM名>              # 还原：把工作副本还原成黄金副本
  $0 --start <VM名>      # 还原后顺便开机
  $0 --snapshot <VM名>   # 反向：从当前(已关机)工作副本创建/更新黄金副本
  $0 -h | --help         # 用法
EOF
    exit "${1:-1}"
}

if [[ $# -eq 0 ]]; then
    usage 1
fi

MODE="restore"
START_AFTER=0
VM_NAME=""

# 逐个解析：flag 可出现在任意位置；未知 flag / VM 名以 - 开头一律报错；
# --start 与 --snapshot 互斥；只接受一个 VM 名。
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)
            usage 0
            ;;
        --snapshot)
            MODE="snapshot"
            ;;
        --start)
            START_AFTER=1
            ;;
        -*)
            echo "[Error] 未知选项: $1"
            usage 1
            ;;
        *)
            if [[ -n "$VM_NAME" ]]; then
                echo "[Error] 只能指定一个 VM 名（已有 '$VM_NAME'，又出现 '$1'）"
                usage 1
            fi
            VM_NAME="$1"
            ;;
    esac
    shift
done

if [[ "$MODE" == "snapshot" && $START_AFTER -eq 1 ]]; then
    echo "[Error] --snapshot 与 --start 不能同时使用"
    usage 1
fi

if [[ -z "$VM_NAME" ]]; then
    echo "[Error] 缺少 <VM名> 参数"
    usage 1
fi

VM_WORK_PATH="$UTM_DOCS/${VM_NAME}.utm"
# Override golden path if UTM_GOLDEN is set entirely, else construct from UTM_GOLDEN_DIR
UTM_GOLDEN="${UTM_GOLDEN:-$UTM_GOLDEN_DIR/${VM_NAME}.utm}"

# ------------------------------------------------------------------------------
# Helper Functions
# ------------------------------------------------------------------------------
check_utmctl() {
    if [[ ! -x "$UTMCTL" ]]; then
        echo "[Error] 找不到可执行的 utmctl: $UTMCTL。请确认已安装 UTM 且路径正确。"
        exit 1
    fi
}

check_status() {
    "$UTMCTL" status "$VM_NAME" 2>/dev/null || echo "unknown"
}

# 解析 VM 是否确实停机。回显三态之一：
#   stopped   —— utmctl 明确报 stopped（安全）
#   running   —— utmctl 报了非 stopped 的运行态
#   ambiguous —— utmctl 读不到状态(unknown) 但 UTM 进程在跑
#                → 无法判定 VM 死活，绝不当"安全"处理（会 rm -rf 掉可能在运行的 VM）
# 关键：utmctl 读失败(unknown) 且 UTM 没在跑时，VM 不可能在运行 → 视为 stopped。
vm_stopped_state() {
    local s
    s=$(check_status)
    if [[ "$s" == "stopped" ]]; then
        echo "stopped"
    elif [[ "$s" == "unknown" ]]; then
        if pgrep -x UTM >/dev/null 2>&1; then
            echo "ambiguous"
        else
            echo "stopped"
        fi
    else
        echo "running"
    fi
}

wait_for_status() {
    local target_status=$1
    local timeout=60
    local i=0
    echo "[Info] 等待 VM 状态变为 '$target_status'..."
    while [[ $i -lt $timeout ]]; do
        local current=$(check_status)
        if [[ "$current" == "$target_status" ]]; then
            return 0
        fi
        sleep 1
        # 用 i=$((i+1)) 而非 ((i++))：后者在 i 自增前为 0 时返回退出码 1，
        # 配 set -e 在某些 bash 版本/位置会误杀脚本。这种写法永不返回非零。
        i=$((i+1))
    done
    echo "[Error] 等待 '$target_status' 超时 (60s)，当前状态: $current"
    exit 1
}

quit_utm_app() {
    echo "[Info] 尝试正常退出 UTM.app..."
    osascript -e 'quit app "UTM"' >/dev/null 2>&1 || true
    sleep 2
    
    if pgrep -x UTM >/dev/null; then
        echo "[Warn] UTM 未能正常退出，尝试强杀..."
        killall UTM >/dev/null 2>&1 || true
        sleep 2
    fi
    
    if pgrep -x UTM >/dev/null; then
        echo "[Error] 无法终止 UTM 进程，退出以防数据损坏。"
        exit 1
    fi
    echo "[Info] UTM 已完全退出。"
}

# 轮询 UTM 是否就绪（utmctl 能应答）。冷启动在 M 系列高负载下可能超过固定
# sleep，所以用轮询替代"sleep 3 就假定 UTM 起来了"这种不可靠假设。
wait_for_utm_ready() {
    local i=0
    while [[ $i -lt 30 ]]; do
        if "$UTMCTL" list >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
        i=$((i+1))
    done
    return 1
}

check_same_volume() {
    local p1="$1"
    local p2="$2"
    
    # 获取父目录（目标文件可能还不存在）
    local p1_dir=$(dirname "$p1")
    local p2_dir=$(dirname "$p2")
    
    mkdir -p "$p1_dir" "$p2_dir"
    
    local dev1=$(stat -f '%d' "$p1_dir")
    local dev2=$(stat -f '%d' "$p2_dir")
    
    if [[ "$dev1" != "$dev2" ]]; then
        echo "[Warn] ==========================================================================="
        echo "[Warn] 警告：源目录与目标目录不在同一个 APFS 卷上！"
        echo "[Warn] 源: $p1_dir"
        echo "[Warn] 目标: $p2_dir"
        echo "[Warn] 'cp -c' 将退化为普通深度拷贝，这可能会花费数分钟并占用大量额外存储空间。"
        echo "[Warn] ==========================================================================="
    else
        echo "[Info] 同卷检测通过，可执行 APFS 瞬时克隆。"
    fi
}

# ------------------------------------------------------------------------------
# Core Logic
# ------------------------------------------------------------------------------
check_utmctl

if [[ "$MODE" == "restore" ]]; then
    # 1. Check if golden snapshot exists
    if [[ ! -d "$UTM_GOLDEN" ]]; then
        echo "[Error] 找不到黄金副本: $UTM_GOLDEN"
        echo "[Error] 请先运行 '$0 --snapshot \"$VM_NAME\"' 创建副本。"
        exit 1
    fi

    # 2. Stop VM —— 只有"明确已停机"才跳过；running 则强停并轮询；
    #    ambiguous（读不到状态但 UTM 在跑）绝不硬来，fail-closed。
    state=$(vm_stopped_state)
    if [[ "$state" == "ambiguous" ]]; then
        echo "[Error] utmctl 读不到 VM 状态但 UTM 正在运行——无法确认 VM 已停机。"
        echo "[Error] 为防对运行中的 VM 执行 rm -rf 导致损坏，拒绝继续。请手动确认 VM 关机后重试。"
        exit 1
    elif [[ "$state" == "running" ]]; then
        echo "[Info] VM 正在运行, 发送强制停止命令 (--kill)..."
        "$UTMCTL" stop --kill "$VM_NAME" >/dev/null 2>&1 || true
        wait_for_status "stopped"
    fi

    # 3. Quit UTM
    quit_utm_app

    # 4. Volume check
    check_same_volume "$UTM_GOLDEN" "$VM_WORK_PATH"

    # 5. Restore (Clone) —— 原子化：先克隆到临时兄弟目录，成功后再删旧、改名。
    #    这样 cp 中途失败（ENOSPC/权限/中断）不会留下"旧的删了、新的没成"的空档：
    #    旧工作副本在 mv 成功前始终完好。
    if [[ "$VM_WORK_PATH" != *.utm ]]; then
        echo "[Error] 工作副本路径异常: $VM_WORK_PATH (未以 .utm 结尾)。为了安全拒绝操作。"
        exit 1
    fi
    VM_WORK_TMP="${VM_WORK_PATH}.new.$$"
    # 失败/中断时清掉半成品临时目录（旧工作副本不受影响）
    trap 'rm -rf "$VM_WORK_TMP" 2>/dev/null || true' EXIT
    rm -rf "$VM_WORK_TMP"
    echo "[Info] 正在从黄金副本克隆到临时目录..."
    cp -c -R "$UTM_GOLDEN" "$VM_WORK_TMP"
    echo "[Info] 克隆成功，替换旧工作副本..."
    rm -rf "$VM_WORK_PATH"
    mv "$VM_WORK_TMP" "$VM_WORK_PATH"
    trap - EXIT
    
    # 6. Restart UTM
    # 克隆此刻已完成——open 失败(如 UTM 未装/路径异常)不该让 set -e 把
    # 已成功的还原判成失败，降级为警告，继续。
    echo "[Info] 重启 UTM.app..."
    open -a UTM || echo "[Warn] 无法自动打开 UTM(是否已安装?)，请手动打开。还原本身已完成。"

    # 7. Optionally start VM —— 用就绪轮询代替固定 sleep：冷启动可能 >3s，
    #    否则 utmctl start 会赛跑失败、被 set -e 误判成还原失败。
    if [[ $START_AFTER -eq 1 ]]; then
        if wait_for_utm_ready; then
            echo "[Info] 启动 VM..."
            "$UTMCTL" start "$VM_NAME"
            wait_for_status "started"
            echo "[Success] 还原完成，VM 已启动！(基线: $UTM_GOLDEN)"
        else
            echo "[Warn] UTM 未在超时内就绪；还原已完成，但未能自动启动，请手动开机。(基线: $UTM_GOLDEN)"
        fi
    else
        echo "[Success] 还原完成，可在 UTM 里手动开机。(基线: $UTM_GOLDEN)"
    fi
    echo "[Info] 提示: 还原后 guest 内状态回到干净基线，可无干扰执行新任务。"

elif [[ "$MODE" == "snapshot" ]]; then
    # 1. Check if working copy exists
    if [[ ! -d "$VM_WORK_PATH" ]]; then
        echo "[Error] 找不到工作副本: $VM_WORK_PATH"
        exit 1
    fi
    
    # 2. Confirm VM is stopped —— 非"明确停机"一律 fail-closed（含 ambiguous，
    #    避免把运行中的脏 .utm 抓成黄金副本）。
    state=$(vm_stopped_state)
    if [[ "$state" != "stopped" ]]; then
        echo "[Error] VM 未确认停机 (状态: $state)。必须先彻底关机，以防捕获到脏状态。"
        [[ "$state" == "ambiguous" ]] && echo "[Error] （utmctl 读不到状态但 UTM 在运行——请手动确认 VM 已关机）"
        exit 1
    fi
    
    # 3. Quit UTM
    quit_utm_app
    
    # 4. Guard golden path
    if [[ "$UTM_GOLDEN" != *.utm ]]; then
        echo "[Error] 黄金副本路径异常: $UTM_GOLDEN (未以 .utm 结尾)。拒绝操作。"
        exit 1
    fi

    # 5. Volume check
    check_same_volume "$VM_WORK_PATH" "$UTM_GOLDEN"

    # 6. Clone to golden —— 原子化：先克隆到临时目录，成功后再替换旧黄金副本，
    #    保证一次失败的 snapshot 绝不毁掉上一个可用的黄金基线。
    GOLDEN_TMP="${UTM_GOLDEN}.new.$$"
    trap 'rm -rf "$GOLDEN_TMP" 2>/dev/null || true' EXIT
    rm -rf "$GOLDEN_TMP"
    echo "[Info] 正在抓取新的黄金副本..."
    cp -c -R "$VM_WORK_PATH" "$GOLDEN_TMP"
    rm -rf "$UTM_GOLDEN"
    mv "$GOLDEN_TMP" "$UTM_GOLDEN"
    trap - EXIT

    echo "[Success] 黄金副本已更新: $UTM_GOLDEN"
    
    echo "[Info] 重启 UTM.app..."
    open -a UTM || echo "[Warn] 无法自动打开 UTM(是否已安装?)，请手动打开。黄金副本已抓取完成。"
fi
