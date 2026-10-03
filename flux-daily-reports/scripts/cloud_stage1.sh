#!/usr/bin/env bash
# F.LUX 技能包 —— 阶段 1「第 1 轮真机全流程」云端一键脚本
#
# 前置：
#   1) bash scripts/cloud_bootstrap.sh   （装依赖 + 起带 CDP 9222 的 Chromium）
#   2) 用 VNC/远程桌面在云端浏览器里**人工登录**牵牛花 + 闪购
#
# 本脚本做三件事：
#   1) 探活两个平台的 CDP 通道（不通就停，不硬跑）
#   2) 只看缺口（check-gap），把待采清单打出来
#   3) 跑完整链路：缺口→采集→清洗入库→自检→汇报（flux run）
#
# 用法：
#   bash scripts/cloud_stage1.sh              # 看缺口 + 跑完整链路
#   bash scripts/cloud_stage1.sh --plan-only  # 只看缺口，不采集
#
# 缺口怎么定：run_daily 按「库里最后一天 + 1 → 今天 - 1」自动规划，
# 所以库里到 09-25、今天 10-01 时，它会自己规划 09-26~09-30 共 5 天。
#
# 环境变量：
#   QIANNIU_CDP / SHANGOU_CDP  CDP 端点（默认 http://127.0.0.1:9222）
#   FLUX_VENV                  venv 目录（默认 <包根>/.venv）
#   FLUX_CONFIG                配置路径（默认 <包根>/config.json）
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
VENV="${FLUX_VENV:-$ROOT/.venv}"
CONFIG="${FLUX_CONFIG:-$ROOT/config.json}"
CDP_PORT="${FLUX_CDP_PORT:-9222}"
RULES="${FLUX_RULES:-$ROOT/references/cleaning_rules.json}"
MASTER="${FLUX_MASTER:-$ROOT/_data/store_master.json}"
DB="${FLUX_DB:-$ROOT/_data/flux.db}"

export QIANNIU_CDP="${QIANNIU_CDP:-http://127.0.0.1:$CDP_PORT}"
export SHANGOU_CDP="${SHANGOU_CDP:-http://127.0.0.1:$CDP_PORT}"

PLAN_ONLY=0
PASSTHRU=()
while [ $# -gt 0 ]; do
  case "$1" in
    --plan-only) PLAN_ONLY=1; shift ;;
    *) PASSTHRU+=("$1"); shift ;;
  esac
done

log() { printf '\033[36m[stage1]\033[0m %s\n' "$*"; }
die() { printf '\033[31m[stage1:错误]\033[0m %s\n' "$*" >&2; exit 1; }

PY="$VENV/bin/python"; [ -x "$PY" ] || PY="$VENV/Scripts/python.exe"
[ -x "$PY" ] || die "找不到 venv：$VENV（先跑 bash scripts/cloud_bootstrap.sh）"
[ -f "$CONFIG" ] || die "找不到配置：$CONFIG（cp references/config.example.json config.json 后按需改）"

TODAY="$(date +%F)"

probe() {
  local name="$1" ep="$2"
  curl -sf "$ep/json/version" >/dev/null 2>&1 || die "$name CDP 不通：$ep"
  log "$name CDP 可达：$ep"
}
probe "牵牛花" "$QIANNIU_CDP"
probe "闪购"   "$SHANGOU_CDP"

log "缺口规划（今天=$TODAY）"
"$PY" "$ROOT/scripts/flux.py" check-gap --today "$TODAY" --config "$CONFIG"

[ "$PLAN_ONLY" -eq 1 ] && { log "--plan-only，已停"; exit 0; }

log "跑完整链路：缺口→采集→清洗入库→自检→汇报"
"$PY" "$ROOT/scripts/flux.py" run \
  --today "$TODAY" --config "$CONFIG" \
  --rules "$RULES" --master "$MASTER" --db "$DB" \
  "${PASSTHRU[@]+"${PASSTHRU[@]}"}"

log "完成。批次清单在 $ROOT/_data/_batches/<batch_id>/manifest.json"
