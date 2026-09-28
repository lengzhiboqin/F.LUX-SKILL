#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
牵牛花数据采集 Skill - 登录态探测
检查浏览器是否已登录牵牛花中台，未登录时引导用户登录
"""

import sys
import json
import logging
import argparse
from pathlib import Path

# 添加脚本目录到路径
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (
    load_config, is_configured, get_skill_dir,
    print_banner, setup_logging, FLUX_KEYWORDS
)

# ============================================================
# 浏览器连接与登录态检查
# ============================================================

def get_chrome_cdp_url(port):
    """获取 Chrome DevTools Protocol URL"""
    return f"http://127.0.0.1:{port}"

def check_chrome_available(port):
    """检查 Chrome 远程调试端口是否可用"""
    import urllib.request
    try:
        url = f"http://127.0.0.1:{port}/json/version"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            return True, data.get("Browser", "unknown")
    except Exception as e:
        return False, str(e)

def get_page_targets(port):
    """获取浏览器中所有页面目标"""
    import urllib.request
    try:
        url = f"http://127.0.0.1:{port}/json"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        logging.error(f"获取页面列表失败: {e}")
        return []

def find_qianniu_page(port):
    """查找牵牛花中台页面"""
    targets = get_page_targets(port)
    for t in targets:
        url = t.get("url", "")
        if "meituan.com" in url and ("qnh" in url or "qianniu" in url or "retail" in url):
            return t
    # 如果没找到精确匹配，找任意 meituan 页面
    for t in targets:
        if "meituan.com" in t.get("url", ""):
            return t
    return None

def check_login_via_api(port):
    """通过 API 检查登录态（需要在牵牛花页面上下文中执行）"""
    # 此函数通过 CDP 在页面中执行 JS 调用登录态接口
    # 实际实现需要 playwright 或 websocket 连接
    # 这里返回占位，实际执行时由浏览器操作模块完成
    return None

# ============================================================
# 主流程
# ============================================================

def run_check(verbose=False):
    """执行登录态检查，返回 (is_logged_in, message)"""
    config = load_config()
    if not config:
        return False, "未找到配置文件，请先运行安装向导"

    port = config.get("chrome_port", 9222)
    base_url = config.get("base_url", "https://qnh.meituan.com")

    # 1. 检查 Chrome 是否可用
    available, browser_info = check_chrome_available(port)
    if not available:
        message = (
            f"Chrome 远程调试端口 {port} 不可用。\n"
            f"请先启动浏览器：\n"
            f"  Linux: google-chrome --remote-debugging-port={port} --user-data-dir=/home/user/qianniu-chrome-profile\n"
            f"  Windows: \"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe\" --remote-debugging-port=9333 --user-data-dir=\"C:\\qianniu-chrome-profile\"\n"
            f"然后访问 {base_url} 完成登录"
        )
        return False, message

    if verbose:
        print(f"✓ Chrome 已连接: {browser_info}")

    # 2. 查找牵牛花页面
    page = find_qianniu_page(port)
    if not page:
        message = (
            f"未在浏览器中找到牵牛花中台页面。\n"
            f"请在浏览器中打开 {base_url} 并完成登录"
        )
        return False, message

    if verbose:
        print(f"✓ 找到牵牛花页面: {page.get('url', '')[:80]}")

    # 3. 检查登录态（通过页面标题和 URL 判断）
    page_url = page.get("url", "")
    page_title = page.get("title", "")

    # 如果页面在登录页，说明未登录
    if "login" in page_url.lower() or "passport" in page_url.lower():
        return False, "牵牛花页面停留在登录页，请完成登录"

    # 如果页面标题包含登录相关字样
    if "登录" in page_title or "login" in page_title.lower():
        return False, "页面标题显示未登录，请完成登录"

    # 基本判断通过（更精确的 API 检查需要浏览器操作模块）
    return True, f"登录态正常（页面: {page_title[:50]}）"

def main():
    parser = argparse.ArgumentParser(description="牵牛花中台登录态探测")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细输出")
    parser.add_argument("--fix", action="store_true", help="未登录时输出修复指引")
    args = parser.parse_args()

    setup_logging()
    print_banner("牵牛花中台登录态探测")

    if not is_configured():
        print("❌ 未找到配置文件，请先运行安装向导:")
        print(f"   bash {get_skill_dir()}/scripts/setup.sh")
        sys.exit(3)

    is_logged_in, message = run_check(verbose=args.verbose)

    if is_logged_in:
        print(f"\n✅ {message}")
        sys.exit(0)
    else:
        print(f"\n❌ 登录态异常: {message}")
        if args.fix:
            print("\n修复步骤:")
            print("1. 确认浏览器已启动（远程调试端口开启）")
            print("2. 在浏览器中打开牵牛花中台并完成登录")
            print("3. 重新运行本脚本验证")
        sys.exit(2)

if __name__ == "__main__":
    main()
