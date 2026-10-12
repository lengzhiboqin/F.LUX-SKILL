#!/usr/bin/env bash
# F.LUX 技能包 —— 把 noVNC 打包成一个「单页应用」目录，供沙箱发布为在线应用。
#
# 背景：沙箱预览面板是「浏览器里的浏览器」，无法连到沙箱 localhost 的 WebSocket。
# 因此必须把整个 VNC 客户端打成一个静态站点，发布成公网应用，用户才能远程看桌面。
#
# 做法：把 /usr/share/novnc 整目录（含 core/rfb.js 等模块）复制到发布目录，
#       并把入口改名为 index.html。websockify --web 同端口同时提供静态资源与 ws 代理。
#
# 用法：
#   bash scripts/cloud_vnc_webapp.sh            # 生成 ./_vncweb
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
OUT="${FLUX_VNC_WEBAPP:-$ROOT/_vncweb}"
NOVNC_WEB="${FLUX_NOVNC_WEB:-/usr/share/novnc}"

[ -d "$NOVNC_WEB" ] || { echo "找不到 noVNC：$NOVNC_WEB" >&2; exit 1; }

rm -rf "$OUT"
mkdir -p "$OUT"
cp -r "$NOVNC_WEB"/. "$OUT"/
# 入口页：vnc_lite 自包含、无需 CSS/图标，作为 index.html
cp "$OUT/vnc_lite.html" "$OUT/index.html"

# 调整 index.html：自动连接 + 自适应缩放 + 大写锁定兜底按钮
python3 - "$OUT/index.html" <<'PY'
import re, sys
p = sys.argv[1]
s = open(p, encoding='utf-8').read()

# 在顶部状态栏加两个按钮：
#   1) 「大写锁定」——发送 CapsLock keysym，解决浏览器抢走 CapsLock 键的问题
#   2) 「粘贴」——把本地剪贴板内容送进云端，免去远程逐字打字
s = s.replace(
    '<div id="sendCtrlAltDelButton">Send CtrlAltDel</div>',
    '<div id="pasteButton" title="把本地剪贴板内容粘贴到云端（也可直接按 Ctrl+V）">粘贴</div>\n'
    '        <div id="capslockButton" title="发送大写锁定（CapsLock）">大写锁定</div>\n'
    '        <div id="sendCtrlAltDelButton">Send CtrlAltDel</div>',
    1,
)
s = s.replace(
    '#sendCtrlAltDelButton {',
    '#pasteButton { float: right; border-left: 1px solid #444; padding: 0px 5px 0px 5px; '
    'cursor: pointer; user-select: none; }\n'
    '        #pasteButton:hover { background: #555; }\n'
    '        #capslockButton { float: right; border-left: 1px solid #444; padding: 0px 5px 0px 5px; '
    'cursor: pointer; user-select: none; }\n'
    '        #capslockButton:hover { background: #555; }\n'
    '        #sendCtrlAltDelButton {',
    1,
)

# 在 RFB 初始化处注入：自动连接 + 缩放 + 连接失败提示
inject = """
        // ---- F.LUX 云端桌面：自动连接与自适应 ----
        const __qs = new URLSearchParams(location.search);
        const __pw  = __qs.get('password') || null;
        const __viewOnly = __qs.get('view_only') === '1';
        const __scale = __qs.get('scale') !== '0';
        try {
            if (__scale) rfb.scaleViewport = true;
            rfb.viewOnly = __viewOnly;
            rfb.background = '#1f2430';

            // ---- 传输质量调优：远程拖动滑块需要「低延迟 + 高帧率」 ----
            // 默认 noVNC 追求画质，压缩级别高 → 延迟大、拖动卡顿。
            // 这里改成偏实时：中等压缩 + 宽松质量，牺牲一点画质换流畅度。
            try {
                rfb.compressionLevel = Number(__qs.get('compress') ?? 2);   // 0-9，越低越快
                rfb.qualityLevel     = Number(__qs.get('quality')  ?? 5);   // 0-9，越高越清晰
            } catch (e) {}

            rfb.addEventListener('connect', () => { document.title = 'F.LUX 云端桌面 — 已连接'; });
            rfb.addEventListener('disconnect', (e) => {
                const d = e.detail || {};
                if (d.clean) return;
                document.title = 'F.LUX 云端桌面 — 连接断开';
                const msg = document.getElementById('noVNC_status');
                if (msg) { msg.textContent = '连接断开：' + (d.name || '未知原因') + '，请刷新页面重试'; msg.className = 'noVNC_status_warn'; }
            });
            rfb.connect('', 0, __pw, '');

            // ---- 物理 CapsLock 键兜底 ----
            // 浏览器会拦截 CapsLock（或不下发 keydown/keyup），导致云端收不到。
            // 这里在 window 捕获阶段监听，手动分帧补发一对 press/release。
            let __capLockBusy = false;
            window.addEventListener('keydown', (e) => {
                if (e.code !== 'CapsLock') return;
                e.preventDefault();
                if (__capLockBusy) return;
                __capLockBusy = true;
                try {
                    rfb.sendKey(0xffe5, 'CapsLock', true);
                    setTimeout(() => {
                        try { rfb.sendKey(0xffe5, 'CapsLock', false); } catch (x) {}
                        __capLockBusy = false;
                    }, 60);
                } catch (x) { __capLockBusy = false; }
            }, true);

            // ---- 剪贴板：把本地文本送进云端（远程打字太慢的替代方案） ----
            // 方式一：点「粘贴」按钮 → 读本地剪贴板 → clipboardPasteFrom
            // 方式二：直接按 Ctrl+V（拦截后走高权限路径，失败则退回按钮方式）
            async function __pasteText(txt) {
                if (!txt) return false;
                try { rfb.clipboardPasteFrom(txt); return true; }
                catch (e) { console.warn('clipboardPasteFrom 失败', e); return false; }
            }
            async function __pasteFromClipboard(btn) {
                try {
                    if (navigator.clipboard && navigator.clipboard.readText) {
                        const txt = await navigator.clipboard.readText();
                        if (txt) { const ok = await __pasteText(txt); if (ok) { if (btn) { btn.textContent='已粘贴'; setTimeout(()=>btn.textContent='粘贴',600);} return true; } }
                    }
                } catch (e) { /* 无权限：走下面的兜底输入框 */ }
                // 兜底：弹一个输入框，让用户手动贴一次（无需剪贴板权限）
                const manual = window.prompt('浏览器未授予剪贴板权限。\\n请在此处粘贴文本（Ctrl+V），确认后送入云端：', '');
                if (manual) { await __pasteText(manual); if (btn) { btn.textContent='已粘贴'; setTimeout(()=>btn.textContent='粘贴',600);} return true; }
                return false;
            }
            const __pasteBtn = document.getElementById('pasteButton');
            if (__pasteBtn) {
                __pasteBtn.addEventListener('click', (ev) => { ev.preventDefault(); __pasteFromClipboard(__pasteBtn); });
            }
            window.addEventListener('keydown', (e) => {
                if ((e.ctrlKey || e.metaKey) && (e.key === 'v' || e.key === 'V')) {
                    // 不放行给 noVNC；改走剪贴板同步
                    e.preventDefault(); e.stopPropagation();
                    __pasteFromClipboard(null);
                }
            }, true);

            // ---- 大写锁定兜底：绕过浏览器对 CapsLock 键的抢占 ----
            // 关键：CapsLock 是「锁键」，X 依赖一对独立的 press/release 来翻转状态。
            // 若 down 与 up 在同一 tick 发出，X 会合并成单次按下，状态不翻转。
            // 因此这里显式分帧发送：先 down，隔一帧再 up。
            const __capBtn = document.getElementById('capslockButton');
            if (__capBtn) {
                let __capBusy = false;
                __capBtn.addEventListener('click', (ev) => {
                    ev.preventDefault();
                    if (__capBusy) return;
                    __capBusy = true;
                    try {
                        rfb.sendKey(0xffe5, 'CapsLock', true);   // press
                        setTimeout(() => {                        // release 延后一帧
                            try { rfb.sendKey(0xffe5, 'CapsLock', false); } catch (e) {}
                            __capBusy = false;
                        }, 60);
                        __capBtn.textContent = '已发送';
                        setTimeout(() => { __capBtn.textContent = '大写锁定'; }, 300);
                    } catch (err) { __capBusy = false; console.warn('capslock 发送失败', err); }
                });
            }
        } catch (err) {
            const msg = document.getElementById('noVNC_status');
            if (msg) { msg.textContent = '连接失败：' + err; msg.className = 'noVNC_status_error'; }
        }
"""
# 找到 updateState('connecting') 与 rfb.connect 调用段，整体替换为注入逻辑
s2 = re.sub(r"updateState\(\s*'connecting'\s*\)\s*;?\s*", "\n        updateState('connecting');\n", s, count=1)
# 替换原本的 rfb.connect(...) 调用
s2 = re.sub(r"rfb\.connect\([^;]*\)\s*;", inject.strip(), s2, count=1)
if s2 == s:
    # 兜底：在 </script> 之前插入
    s2 = s.replace('</script>', inject + '\n    </script>', 1)
open(p, 'w', encoding='utf-8').write(s2)
print('index.html 已注入自动连接逻辑')
PY

echo "发布目录就绪：$OUT"
echo "启动：websockify --web $OUT \$PORT 127.0.0.1:5900"
