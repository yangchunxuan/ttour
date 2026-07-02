#!/bin/bash
# run_broker.sh — 在**宿主 Mac** 上起 broker（spec §2A.3）。
#
# 每次运行轮换 bearer token（§2A.3.2）。真 DeepSeek key 从 BROKER_UPSTREAM_KEY
# 读，绝不写死、绝不进 VM。用专用、可吊销、低配额的 key（§2A.3.6）。
#
# host-only 网络示例（§2A.3.1）：
#   BROKER_BIND    = 宿主在 host-only 网卡上的地址（如 192.168.66.1）
#   BROKER_ALLOW_IPS = 该 VM guest 的 host-only IP（如 192.168.66.2）
# 只监听回环时 BROKER_BIND 留默认 127.0.0.1、ALLOW_IPS 留空即可（本机联调）。
set -euo pipefail

if [[ -z "${BROKER_UPSTREAM_KEY:-}" ]]; then
  echo "请先 export BROKER_UPSTREAM_KEY=sk-...（专用可吊销低配额 DeepSeek key）" >&2
  exit 2
fi

# 每次运行轮换 token（§2A.3.2）
export BROKER_TOKEN="${BROKER_TOKEN:-$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')}"

echo "broker bearer token（本次运行）：$BROKER_TOKEN"
echo "→ 在 VM 端设：BROKER_TOKEN=$BROKER_TOKEN  BROKER_BASE_URL=http://<宿主地址>:${BROKER_PORT:-8899}/v1"
echo ""

HERE="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "${HERE}/broker.py"
