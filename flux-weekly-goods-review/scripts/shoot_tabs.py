# -*- coding: utf-8 -*-
"""
六个页签的无头截图（自检用，不进交付）。
用法：python shoot_tabs.py --root "...\\每周货盘与新品复盘会议" --out "<临时目录>" [--tag 5]
产物：overview.png / new.png / activity.png / structure.png / actions.png / targets.png
"""
import argparse, os, subprocess, sys, time

sys.stdout.reconfigure(encoding="utf-8")

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SIZES = {
    "overview": (1120, 1400),
    "new": (1120, 2150),
    "activity": (1120, 1500),
    "structure": (1120, 1800),
    "actions": (1120, 1600),
    "targets": (1120, 1500),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tag", default=str(int(time.time())))
    a = ap.parse_args()

    html = os.path.join(os.path.abspath(a.root), "看板", "商品周复盘看板.html").replace("\\", "/")
    if not os.path.exists(html.replace("/", "\\")):
        print("[FATAL] 缺看板：%s" % html); sys.exit(2)
    if not os.path.exists(CHROME):
        print("[FATAL] 找不到 Chrome：%s" % CHROME); sys.exit(2)
    os.makedirs(a.out, exist_ok=True)

    for tab, (w, h) in SIZES.items():
        png = os.path.join(a.out, "%s.png" % tab)
        url = "file:///%s?v=%s#%s" % (html.lstrip("/"), a.tag, tab)
        cmd = [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
               "--force-device-scale-factor=1", "--virtual-time-budget=3000",
               "--window-size=%d,%d" % (w, h), "--screenshot=%s" % png, url]
        r = subprocess.run(cmd, capture_output=True, text=True)
        ok = os.path.exists(png) and os.path.getsize(png) > 50 * 1024
        print("%-10s %s  %s" % (tab, "ok" if ok else "FAIL",
                                ("%d KB" % (os.path.getsize(png) // 1024)) if ok else (r.stderr.strip()[:80])), flush=True)
    print("输出目录：%s" % a.out)


if __name__ == "__main__":
    main()
