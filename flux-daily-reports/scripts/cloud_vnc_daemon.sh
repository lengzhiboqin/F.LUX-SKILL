#!/usr/bin/env bash
# F.LUX 技能包 —— 云端 VNC 桌面「守护式」启动（发布给用户远程登录用）
#
# 与 cloud_vnc.sh 的区别：本脚本额外负责
#   1) 生成发布用的静态站点（_vncweb）
#   2) 用 setsid + 重定向彻底脱离终端，避免随命令结束被杀
#   3) 启动一个自愈看门狗：websockify / x11vnc / chromium / Xvfb 掉线自动拉起
#
# 用法：
#   bash scripts/cloud_vnc_daemon.sh          # 起全套 + 看门狗
#   bash scripts/cloud_vnc_daemon.sh --status # 看状态
#   bash scripts/cloud_vnc_daemon.sh --stop   # 全停
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
WEBAPP="${FLUX_VNC_WEBAPP:-$ROOT/_vncweb}"
WEB_PORT="${FLUX_VNC_WEB_PORT:-3000}"
RFB_PORT="${FLUX_RFB_PORT:-5900}"
CDP_PORT="${FLUX_CDP_PORT:-9222}"
DISPLAY_NUM="${FLUX_DISPLAY:-:99}"
WATCH_PIDFILE="$HOME/.flux-vnc/watchdog.pid"
mkdir -p "$HOME/.flux-vnc"

log() { printf '\033[36m[vnc-daemon]\033[0m %s\n' "$*"; }

up() { curl -sf "$1" >/dev/null 2>&1; }

status() {
  printf '%-12s %s\n' "Xvfb"     "$(pgrep -f "Xvfb $DISPLAY_NUM" >/dev/null && echo 运行中 || echo 未运行)"
  printf '%-12s %s\n' "Chromium" "$(up "http://127.0.0.1:$CDP_PORT/json/version" && echo 运行中 || echo 未运行)"
  printf '%-12s %s\n' "x11vnc"   "$(pgrep -f "x11vnc.*$DISPLAY_NUM" >/dev/null && echo 运行中 || echo 未运行)"
  printf '%-12s %s\n' "noVNC"    "$(up "http://127.0.0.1:$WEB_PORT/" && echo 运行中 || echo 未运行)"
  printf '%-12s %s\n' "看门狗"   "$([ -f "$WATCH_PIDFILE" ] && kill -0 "$(cat "$WATCH_PIDFILE")" 2>/dev/null && echo 运行中 || echo 未运行)"
}

case "${1:-}" in
  --status) status; exit 0 ;;
  --stop)
    [ -f "$WATCH_PIDFILE" ] && kill "$(cat "$WATCH_PIDFILE")" 2>/dev/null || true
    rm -f "$WATCH_PIDFILE"
    bash "$ROOT/scripts/cloud_vnc.sh" --stop
    log "已全停"; exit 0 ;;
esac

# 0) 生成发布站点
bash "$ROOT/scripts/cloud_vnc_webapp.sh" >/dev/null

# 1) 先起 Xvfb / Chromium / x11vnc（复用 cloud_vnc.sh，含 noVNC 自检端口）
bash "$ROOT/scripts/cloud_vnc.sh" >/dev/null

# 2) 杀掉 cloud_vnc.sh 起的演示端口 noVNC，避免端口冲突；只保留发布用 3000
pkill -f "websockify --web /usr/share/novnc" 2>/dev/null || true
pkill -f "websockify --web $WEBAPP" 2>/dev/null || true
sleep 1

# 3) 看门狗：掉线自动拉起
cat > "$HOME/.flux-vnc/watchdog.sh" <<WATCH
#!/usr/bin/env bash
ROOT="$ROOT"; WEBAPP="$WEBAPP"; WEB_PORT="$WEB_PORT"; RFB_PORT="$RFB_PORT"
CDP_PORT="$CDP_PORT"; DISPLAY_NUM="$DISPLAY_NUM"
up(){ curl -sf "\$1" >/dev/null 2>&1; }
while true; do
  # Xvfb
  pgrep -f "Xvfb \$DISPLAY_NUM" >/dev/null || \\
    { setsid Xvfb "\$DISPLAY_NUM" -screen 0 1280x800x24 -ac +extension RANDR >/dev/null 2>&1 </dev/null & sleep 2; }
  export DISPLAY="\$DISPLAY_NUM"
  # Chromium
  up "http://127.0.0.1:\$CDP_PORT/json/version" || {
    CHROME="\$(command -v google-chrome || command -v chromium)"
    setsid "\$CHROME" --remote-debugging-port="\$CDP_PORT" --remote-debugging-address=127.0.0.1 \\
      --user-data-dir="\$HOME/.flux-chrome-profile" --no-first-run --no-default-browser-check \\
      --no-sandbox --disable-dev-shm-usage --disable-background-timer-throttling --disable-renderer-backgrounding --window-size=1280,800 \\
      about:blank >/dev/null 2>&1 </dev/null & sleep 4; }
  # x11vnc
  pgrep -f "x11vnc.*\$DISPLAY_NUM" >/dev/null || \\
    { setsid x11vnc -display "\$DISPLAY_NUM" -rfbport "\$RFB_PORT" -forever -shared -nopw -wait 20 -defer 20 -threads -norepeat -noxdamage >/dev/null 2>&1 </dev/null & sleep 2; }
  # 剪贴板同步守护（本地 <-> 云端 双向）
  pgrep -f "autocutsel" >/dev/null || \\
    { setsid autocutsel -fork >/dev/null 2>&1 </dev/null & sleep 1; }
  pgrep -f "autocutsel -selection PRIMARY" >/dev/null || \\
    { setsid autocutsel -selection PRIMARY -fork >/dev/null 2>&1 </dev/null & sleep 1; }
  # websockify（发布端口）
  up "http://127.0.0.1:\$WEB_PORT/" || \\
    { setsid websockify --web "\$WEBAPP" "\$WEB_PORT" "127.0.0.1:\$RFB_PORT" >/dev/null 2>&1 </dev/null & sleep 2; }
  sleep 5
done
WATCH
chmod +x "$HOME/.flux-vnc/watchdog.sh"
setsid "$HOME/.flux-vnc/watchdog.sh" >/dev/null 2>&1 < /dev/null &
echo $! > "$WATCH_PIDFILE"

for _ in $(seq 1 20); do sleep 1; up "http://127.0.0.1:$WEB_PORT/" && break; done
up "http://127.0.0.1:$WEB_PORT/" || { log "发布端口未就绪"; exit 1; }
log "远程桌面就绪（含看门狗自愈）"
status
