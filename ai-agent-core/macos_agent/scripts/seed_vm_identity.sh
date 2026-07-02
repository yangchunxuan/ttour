#!/bin/bash
# seed_vm_identity.sh — 在 VM guest 内、抓黄金镜像前运行（需 sudo）。
#
# 落两样东西（spec §2A.1 信号 B + §2A.4 干净启动 token）：
#   1. /etc/macos_agent/vm_secret   root 拥有、0600、非空 —— 守卫的信号 B。
#   2. ~/.macos_agent/clean_token   一次性干净启动 token —— run_agent 每次消费。
#
# 关键顺序（spec §3.4）：装系统 → 装依赖 → 在 VM 内授权(辅助功能/屏幕录制)
#   → **跑本脚本** → **抓黄金镜像**。TCC 授权存在 guest 的 TCC 库里，
#   镜像必须在授权之后再抓，否则每次还原都从"未授权"重来。
#
# 用法（在 VM 内）：sudo ./seed_vm_identity.sh
set -euo pipefail

SECRET_DIR="/etc/macos_agent"
SECRET_PATH="${SECRET_DIR}/vm_secret"
TOKEN_DIR="${HOME}/.macos_agent"
TOKEN_PATH="${TOKEN_DIR}/clean_token"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "必须用 sudo 运行（要写 root 拥有的 0600 secret）。" >&2
  exit 1
fi

# 1) 信号 B：root 拥有、0600 的非空 secret
mkdir -p "$SECRET_DIR"
# 用 openssl/头部随机源生成，避免依赖；内容不需要保密于 agent（守卫只 stat
# 存在性/属主/权限/非空），但不世界可写是关键（spec §2A.1：别放 /Users/Shared）。
head -c 48 /dev/urandom | xxd -p | tr -d '\n' > "$SECRET_PATH"
chown root:wheel "$SECRET_PATH"
chmod 0600 "$SECRET_PATH"
echo "✅ 信号 B secret 已写: $SECRET_PATH (root:wheel 0600)"

# 2) 干净启动 token（属主是真实登录用户，不是 root）
REAL_USER="${SUDO_USER:-$(whoami)}"
REAL_HOME="$(eval echo "~${REAL_USER}")"
TOKEN_DIR="${REAL_HOME}/.macos_agent"
TOKEN_PATH="${TOKEN_DIR}/clean_token"
mkdir -p "$TOKEN_DIR"
date +%s > "$TOKEN_PATH"
chown -R "$REAL_USER" "$TOKEN_DIR"
chmod 0644 "$TOKEN_PATH"
echo "✅ 干净启动 token 已写: $TOKEN_PATH"

cat <<EOF

下一步（spec §3.4 顺序，务必遵守）：
  - 确认已在 系统设置 → 隐私与安全性 里给"启动代理的那个程序"
    （Terminal.app 或你的签名 .app 壳）勾了【辅助功能】(命脉) 和【屏幕录制】(仅截图)。
  - 现在关机 VM，**抓黄金镜像/快照**。
  - 之后每次真跑：从黄金镜像还原 → clean_token 回到未消费状态 → run_agent 放行。
EOF
