#!/usr/bin/env bash
# F.LUX 技能包 —— 云端环境引导脚本
#
# 作用：在**云端 Linux 宿主**上把采集链跑起来所需的一切准备好：
#   1) 校验 Node ≥ 24（只读查询用内置 node:sqlite）
#   2) 建隔离 venv 并装 requirements.txt
#   3) 装 Playwright Chromium
#   4) 起一个带 CDP 端口 9222 的 Chromium（供牵牛花 + 闪购连接）
#
# 用法：
#   bash scripts/cloud_bootstrap.sh            # 全量：装依赖 + 起浏览器
#   bash scripts/cloud_bootstrap.sh --browser  # 只起浏览器（依赖已装时）
#
# 起完浏览器后，用 VNC/远程桌面打开它，人工登录：
#   - 牵牛花：https://qnh.meituan.com/
#   - 闪购：  https://shangoue.meituan.com/
# 登录态保存在 $FLUX_PROFILE_DIR（默认 ~/.flux-chrome-profile），**重启浏览器不丢**。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"

CDP_PORT="${FLUX_CDP_PORT:-9222}"
PROFILE_DIR="${FLUX_PROFILE_DIR:-$HOME/.flux-chrome-profile}"
VENV_DIR="${FLUX_VENV:-$ROOT/.venv}"
ONLY_BROWSER=0
[ "${1:-}" = "--browser" ] && ONLY_BROWSER=1

log() { printf '\033[36m[flux]\033[0m %s\n' "$*"; }
die() { printf '\033[31m[flux:错误]\033[0m %s\n' "$*" >&2; exit 1; }

# ---------- 1) Node 版本 ----------
check_node() {
  command -v node >/dev/null 2>&1 || die "未找到 node，请先装 Node ≥ 24"
  local major; major="$(node -p 'process.versions.node.split(".")[0]')"
  [ "$major" -ge 24 ] || die "Node 版本过低（当前 $(node -v)），只读查询需 ≥ 24"
  log "Node $(node -v) OK"
}

# ---------- 2) Python 依赖 ----------
setup_python() {
  python3 -m venv "$VENV_DIR"
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
  python -m pip install -q --upgrade pip
  python -m pip install -q -r "$ROOT/requirements.txt"
  python -c "import openpyxl, websocket" || die "依赖导入失败"
  log "Python 依赖装好：$VENV_DIR"
}

# ---------- 3) Playwright Chromium ----------
setup_playwright() {
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
  python -m playwright install chromium
  log "Playwright Chromium 装好"
}

# ---------- 4) 起带 CDP 的 Chromium ----------
start_browser() {
  if curl -sf "http://127.0.0.1:$CDP_PORT/json/version" >/dev/null 2>&1; then
    log "CDP 端口 $CDP_PORT 已有浏览器在响应，跳过启动"
    return
  fi
  # 找 chromium 可执行文件（Playwright 装的在 ms-playwright 下）
  local chrome=""
  for c in "${FLUX_CHROME:-}" "$(command -v chromium 2>/dev/null || true)" \
           "$(command -v chromium-browser 2>/dev/null || true)" \
           "$(command -v google-chrome 2>/dev/null || true)"; do
    [ -n "$c" ] && [ -x "$c" ] && { chrome="$c"; break; }
  done
  if [ -z "$chrome" ]; then
    chrome="$(find "$HOME/.cache/ms-playwright" -type f -name 'chrome' 2>/dev/null | head -1 || true)"
  fi
  [ -n "$chrome" ] || die "找不到 chromium 可执行文件；可设 FLUX_CHROME 指定"

  mkdir -p "$PROFILE_DIR"
  log "启动 Chromium（CDP $CDP_PORT，profile $PROFILE_DIR）"
  nohup "$chrome" \
    --remote-debugging-port="$CDP_PORT" \
    --remote-debugging-address=127.0.0.1 \
    --user-data-dir="$PROFILE_DIR" \
    --no-first-run --no-default-browser-check \
    --disable-dev-shm-usage \
    --window-size=1440,900 \
    about:blank > "$PROFILE_DIR/chrome.log" 2>&1 &

  for _ in $(seq 1 30); do
    sleep 1
    if curl -sf "http://127.0.0.1:$CDP_PORT/json/version" >/dev/null 2>&1; then
      log "浏览器就绪：http://127.0.0.1:$CDP_PORT"
      return
    fi
  done
  die "浏览器启动超时，看日志：$PROFILE_DIR/chrome.log"
}

check_node
if [ "$ONLY_BROWSER" -eq 0 ]; then
  setup_python
  setup_playwright
fi
start_browser

cat <<EOF

=================== 下一步 ===================
1) 用 VNC / 远程桌面打开云端浏览器窗口
2) 人工登录两个后台（登录态会存进 $PROFILE_DIR）：
   - 牵牛花：https://qnh.meituan.com/
   - 闪购：  https://shangoue.meituan.com/
3) 登录完成后跑：
   export QIANNIU_CDP=http://127.0.0.1:$CDP_PORT
   export SHANGOU_CDP=http://127.0.0.1:$CDP_PORT
   python scripts/flux.py check-gap --today \$(date +%F) --config config.json
==============================================
EOF
