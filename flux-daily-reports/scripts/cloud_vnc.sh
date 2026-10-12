#!/usr/bin/env bash
# F.LUX 技能包 —— 云端 noVNC 远程桌面（供用户人工登录两平台）
#
# 解决：云端沙箱无远程桌面，用户看不到容器里的浏览器窗口。
# 做法：Xvfb 虚拟显示 → Chromium 带 CDP 跑在上面 → x11vnc 暴露 → noVNC 转 WebSocket。
#
# 用法：
#   bash scripts/cloud_vnc.sh           # 起全套（幂等，已在跑的就跳过）
#   bash scripts/cloud_vnc.sh --status  # 看状态
#   bash scripts/cloud_vnc.sh --stop    # 停掉
#
# 环境变量：
#   FLUX_DISPLAY      Xvfb 显示号，默认 :99
#   FLUX_VNC_PORT     noVNC 网页端口，默认 6080
#   FLUX_CDP_PORT     Chromium CDP 端口，默认 9222
#   FLUX_PROFILE_DIR  浏览器 profile，默认 ~/.flux-chrome-profile
#   FLUX_CHROME       指定 chromium 可执行文件
set -euo pipefail

DISPLAY_NUM="${FLUX_DISPLAY:-:99}"
SCREEN="${FLUX_SCREEN:-1280x800x24}"
VNC_PORT="${FLUX_VNC_PORT:-6080}"
VNC_ONLY_PORT="${FLUX_RFB_PORT:-5900}"
CDP_PORT="${FLUX_CDP_PORT:-9222}"
PROFILE_DIR="${FLUX_PROFILE_DIR:-$HOME/.flux-chrome-profile}"
RUN_DIR="${FLUX_VNC_RUNDIR:-$HOME/.flux-vnc}"
NOVNC_WEB="${FLUX_NOVNC_WEB:-/usr/share/novnc}"

mkdir -p "$RUN_DIR" "$PROFILE_DIR"
LOG="$RUN_DIR/vnc.log"
: > "$LOG"

log() { printf '\033[36m[vnc]\033[0m %s\n' "$*" | tee -a "$LOG"; }
die() { printf '\033[31m[vnc:错误]\033[0m %s\n' "$*" >&2; exit 1; }

ok() { curl -sf "$1" >/dev/null 2>&1; }

status() {
  echo "Xvfb      : $(pgrep -f "Xvfb $DISPLAY_NUM" >/dev/null && echo 运行中 || echo 未运行)  ($DISPLAY_NUM)"
  echo "Chromium  : $(ok "http://127.0.0.1:$CDP_PORT/json/version" && echo 运行中 || echo 未运行)  (CDP $CDP_PORT)"
  echo "x11vnc    : $(pgrep -f "x11vnc.*$DISPLAY_NUM" >/dev/null && echo 运行中 || echo 未运行)  (RFB $VNC_ONLY_PORT)"
  echo "noVNC     : $(ok "http://127.0.0.1:$VNC_PORT/vnc.html" && echo 运行中 || echo 未运行)  (HTTP $VNC_PORT)"
}

case "${1:-}" in
  --status) status; exit 0 ;;
  --stop)
    pkill -f "websockify.*$VNC_PORT" 2>/dev/null || true
    pkill -f "x11vnc.*$DISPLAY_NUM" 2>/dev/null || true
    pkill -f "remote-debugging-port=$CDP_PORT" 2>/dev/null || true
    pkill -f "Xvfb $DISPLAY_NUM" 2>/dev/null || true
    log "已停止"; exit 0 ;;
esac

# ---------- 1) Xvfb ----------
if pgrep -f "Xvfb $DISPLAY_NUM" >/dev/null; then
  log "Xvfb $DISPLAY_NUM 已在运行"
else
  nohup Xvfb "$DISPLAY_NUM" -screen 0 "$SCREEN" -ac +extension RANDR \
    > "$RUN_DIR/xvfb.log" 2>&1 &
  for _ in $(seq 1 20); do sleep 0.5; pgrep -f "Xvfb $DISPLAY_NUM" >/dev/null && break; done
  pgrep -f "Xvfb $DISPLAY_NUM" >/dev/null || die "Xvfb 启动失败，看 $RUN_DIR/xvfb.log"
  log "Xvfb 就绪：$DISPLAY_NUM ($SCREEN)"
fi
export DISPLAY="$DISPLAY_NUM"

# ---------- 2) Chromium（带 CDP） ----------
if ok "http://127.0.0.1:$CDP_PORT/json/version"; then
  log "CDP $CDP_PORT 已有浏览器，跳过启动"
else
  chrome=""
  for c in "${FLUX_CHROME:-}" "$(command -v chromium 2>/dev/null || true)" \
           "$(command -v google-chrome 2>/dev/null || true)" \
           "$(command -v chromium-browser 2>/dev/null || true)"; do
    [ -n "$c" ] && [ -x "$c" ] && { chrome="$c"; break; }
  done
  [ -z "$chrome" ] && chrome="$(find "$HOME/.cache/ms-playwright" -type f -name 'chrome' 2>/dev/null | head -1 || true)"
  [ -n "$chrome" ] || die "找不到 chromium 可执行文件；可设 FLUX_CHROME 指定"

  nohup "$chrome" \
    --remote-debugging-port="$CDP_PORT" \
    --remote-debugging-address=127.0.0.1 \
    --user-data-dir="$PROFILE_DIR" \
    --no-first-run --no-default-browser-check --no-sandbox \
    --disable-dev-shm-usage \
    --disable-background-timer-throttling \
    --disable-backgrounding-occluded-windows \
    --disable-renderer-backgrounding \
    --disable-features=CalculateNativeWinOcclusion \
    --window-size=1280,800 --start-maximized \
    --window-position=0,0 about:blank > "$RUN_DIR/chrome.log" 2>&1 &
  for _ in $(seq 1 40); do
    sleep 1
    ok "http://127.0.0.1:$CDP_PORT/json/version" && break
  done
  ok "http://127.0.0.1:$CDP_PORT/json/version" || die "浏览器启动超时，看 $RUN_DIR/chrome.log"
  log "Chromium 就绪：CDP http://127.0.0.1:$CDP_PORT"
fi

# ---------- 3) x11vnc ----------
if pgrep -f "x11vnc.*$DISPLAY_NUM" >/dev/null; then
  log "x11vnc 已在运行"
else
  # 性能调优（针对远程拖动滑块/验证码场景）：
  #   -wait 20 -defer 20  合并短时间内的多次更新，降低带宽峰值、提升流畅度
  #   -threads   多线程编码
  #   -noxdamage 关闭 X DAMAGE：Xvfb 下 DAMAGE 扩展不可靠，会导致画面不刷新
  #   ⚠️ 不要开 -ncache：在 Xvfb 上会产生「竖向撕裂/错位」的绘制错误，
  #      官方日志亦警告 "may produce painting errors"。宁可少省带宽，也要画面正确。
  nohup x11vnc -display "$DISPLAY_NUM" -rfbport "$VNC_ONLY_PORT" \
    -forever -shared -nopw \
    -wait 20 -defer 20 -threads -norepeat -noxdamage \
    > "$RUN_DIR/x11vnc.log" 2>&1 &
  sleep 2
  pgrep -f "x11vnc.*$DISPLAY_NUM" >/dev/null || die "x11vnc 启动失败，看 $RUN_DIR/x11vnc.log"
  log "x11vnc 就绪：RFB 127.0.0.1:$VNC_ONLY_PORT"
fi

# ---------- 4) noVNC ----------
if ok "http://127.0.0.1:$VNC_PORT/vnc.html"; then
  log "noVNC 已在运行"
else
  [ -d "$NOVNC_WEB" ] || die "找不到 noVNC 网页目录：$NOVNC_WEB"
  nohup websockify --web "$NOVNC_WEB" "$VNC_PORT" "127.0.0.1:$VNC_ONLY_PORT" \
    > "$RUN_DIR/novnc.log" 2>&1 &
  for _ in $(seq 1 20); do
    sleep 0.5
    ok "http://127.0.0.1:$VNC_PORT/vnc.html" && break
  done
  ok "http://127.0.0.1:$VNC_PORT/vnc.html" || die "noVNC 启动失败，看 $RUN_DIR/novnc.log"
  log "noVNC 就绪：http://127.0.0.1:$VNC_PORT/vnc.html"
fi

cat <<EOF

=================== 远程桌面已就绪 ===================
浏览器窗口（VNC 内可见）已打开，请人工登录：
  牵牛花：https://qnh.meituan.com/
  闪购：  https://shangoue.meituan.com/

登录态保存在：$PROFILE_DIR （重启浏览器不丢）
CDP 端点：    http://127.0.0.1:$CDP_PORT
查看状态：    bash scripts/cloud_vnc.sh --status
=====================================================
EOF
