#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""浏览器CDP启动引导与登录态验证。跨平台。

用法：
  python3 browser_setup.py check                 # 检测CDP与登录态
  python3 browser_setup.py launch-guide          # 输出各平台Chrome启动命令
  python3 browser_setup.py verify-login --port 9222
  python3 browser_setup.py auto-login --phone 13800138000  # 引导短信验证码登录
"""

import argparse
import json
import os
import platform
import shutil
import socket
import sys
import time
import urllib.request
from pathlib import Path

REPORT_URL = "https://shangoue.meituan.com"
LOGIN_URL = "https://passport.meituan.com/account/unitivelogin"
LOGIN_KEYWORDS = ["login", "waimaie", "passport"]


def port_open(port, host="127.0.0.1", timeout=2):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        rc = s.connect_ex((host, port))
        s.close()
        return rc == 0
    except Exception:
        return False


def get_cdp_tabs(port, host="127.0.0.1"):
    try:
        resp = urllib.request.urlopen(f"http://{host}:{port}/json", timeout=5)
        return json.loads(resp.read())
    except Exception:
        return []


def get_browser_version(port, host="127.0.0.1"):
    try:
        resp = urllib.request.urlopen(f"http://{host}:{port}/json/version", timeout=5)
        return json.loads(resp.read())
    except Exception:
        return None


def find_chrome_path():
    system = platform.system()
    candidates = []
    if system == "Windows":
        for pf in [os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                   os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                   os.environ.get("LOCALAPPDATA", "")]:
            if pf:
                candidates += [
                    str(Path(pf) / "Google" / "Chrome" / "Application" / "chrome.exe"),
                    str(Path(pf) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
                ]
    elif system == "Darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
    else:
        for cmd in ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge"]:
            p = shutil.which(cmd)
            if p:
                candidates.append(p)
    for c in candidates:
        if c and Path(c).exists():
            return c
    return None


def get_launch_command(port=9222):
    system = platform.system()
    chrome = find_chrome_path()
    debug_arg = f"--remote-debugging-port={port}"
    profile = str(Path.home() / "shangou-browser-profile")
    if system == "Windows":
        if chrome:
            return f'"{chrome}" {debug_arg} --user-data-dir="{profile}"'
        return f"chrome.exe {debug_arg}"
    if chrome:
        return f'"{chrome}" {debug_arg} --user-data-dir="{profile}"'
    return f"google-chrome {debug_arg}"


def check_cdp(ports=None):
    if ports is None:
        ports = [9222, 9223, 9224]
    result = {"available": False, "port": None, "browser": None, "tabs": []}
    for port in ports:
        if port_open(port):
            version = get_browser_version(port)
            result["available"] = True
            result["port"] = port
            result["browser"] = (version or {}).get("Browser", "unknown")
            result["tabs"] = [
                {"url": t.get("url", ""), "title": t.get("title", "")}
                for t in get_cdp_tabs(port) if t.get("type") == "page"
            ]
            break
    return result


def verify_login(port):
    tabs = get_cdp_tabs(port)
    sg = [t for t in tabs if "shangoue.meituan.com" in t.get("url", "")]
    if not sg:
        return {"logged_in": False, "status": "no_tab",
                "message": "未发现闪购商家端标签页，请打开 " + REPORT_URL}
    for tab in sg:
        if any(kw in tab.get("url", "") for kw in LOGIN_KEYWORDS):
            return {"logged_in": False, "status": "login_page",
                    "message": "登录态已失效，请人工登录", "url": tab.get("url")}
    return {"logged_in": True, "status": "ok", "message": "登录态正常",
            "tabs": [t.get("url", "")[:100] for t in sg]}


def auto_login_via_playwright(port, phone):
    """通过 Playwright 连接 CDP，引导用户完成短信验证码登录。

    流程：
    1. 连接浏览器，打开登录页
    2. 输入手机号
    3. 点击发送验证码
    4. 等待用户提供验证码
    5. 输入验证码并登录
    6. 验证登录态

    注意：滑块/人机验证仍需人工完成。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"success": False, "message": "需要 playwright: pip install playwright"}

    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=15000)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()

        print(f"[auto-login] 正在打开登录页...")
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=45000)
        time.sleep(3)

        # 输入手机号
        print(f"[auto-login] 正在输入手机号: {phone[:3]}****{phone[-4:]}")
        # 尝试多种手机号输入框选择器
        phone_input = None
        for sel in ["input[placeholder*='手机号']", "input[placeholder*='手机']",
                     "input[type='tel']", "input[name='phone']", "input[name='mobile']"]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=3000):
                    phone_input = el
                    break
            except Exception:
                continue

        if not phone_input:
            return {"success": False, "message": "未找到手机号输入框，请人工登录"}

        phone_input.fill(phone)
        time.sleep(1)

        # 点击发送验证码
        print("[auto-login] 正在发送验证码...")
        sms_sent = False
        for sel in ["button:has-text('获取验证码')", "button:has-text('发送验证码')",
                     "a:has-text('获取验证码')", "span:has-text('获取验证码')"]:
            try:
                page.locator(sel).first.click(timeout=3000)
                sms_sent = True
                break
            except Exception:
                continue

        if not sms_sent:
            return {"success": False, "message": "未找到发送验证码按钮，请人工登录"}

        print("\n" + "=" * 50)
        print("  验证码已发送到手机，请在下方输入收到的验证码")
        print("=" * 50)
        code = input("验证码: ").strip()

        if not code:
            return {"success": False, "message": "未输入验证码"}

        # 输入验证码
        sms_input = None
        for sel in ["input[placeholder*='验证码']", "input[placeholder*='短信']",
                     "input[name='captcha']", "input[name='smsCode']"]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=3000):
                    sms_input = el
                    break
            except Exception:
                continue

        if not sms_input:
            return {"success": False, "message": "未找到验证码输入框"}

        sms_input.fill(code)
        time.sleep(1)

        # 点击登录按钮
        for sel in ["button:has-text('登录')", "button:has-text('登 录')",
                     "a:has-text('登录')", "button[type='submit']"]:
            try:
                page.locator(sel).first.click(timeout=3000)
                break
            except Exception:
                continue

        print("[auto-login] 等待登录完成...")
        time.sleep(8)

        # 验证是否登录成功
        current_url = page.url
        if "login" not in current_url and "passport" not in current_url:
            # 跳转到闪购页面
            page.goto(REPORT_URL, wait_until="domcontentloaded", timeout=45000)
            time.sleep(5)
            return {"success": True, "message": "登录成功", "url": page.url}
        else:
            return {"success": False, "message": "登录未成功（可能需要滑块/人工验证），请人工完成",
                    "url": current_url}

    except Exception as e:
        return {"success": False, "message": f"自动登录失败: {str(e)[:200]}"}
    finally:
        pw.stop()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["check", "launch-guide", "verify-login", "auto-login"])
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--phone", help="手机号（auto-login时使用）")
    args = ap.parse_args()

    if args.action == "launch-guide":
        print(json.dumps({
            "os": platform.system(), "chrome_path": find_chrome_path(),
            "command": get_launch_command(args.port), "report_url": REPORT_URL,
            "note": "启动后人工登录闪购商家端，登录/滑块必须人工完成",
        }, ensure_ascii=False, indent=2))
    elif args.action == "check":
        result = check_cdp()
        if result["available"]:
            result["login"] = verify_login(result["port"])
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result["available"] else 1)
    elif args.action == "verify-login":
        if not port_open(args.port):
            print(json.dumps({"logged_in": False, "message": f"CDP端口{args.port}不可用"}, ensure_ascii=False))
            sys.exit(1)
        result = verify_login(args.port)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result["logged_in"] else 1)
    elif args.action == "auto-login":
        if not args.phone:
            print("错误：auto-login 需要 --phone <手机号>", file=sys.stderr)
            sys.exit(1)
        result = auto_login_via_playwright(args.port, args.phone)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()
