#!/usr/bin/env python3
"""
小红书图文笔记自动发布工具（Playwright 独立 CLI 版本）
适用于任意 AI 环境，只需安装 Playwright 即可运行。

安装依赖：
    pip install playwright
    playwright install chromium

使用方式：
    python xhs_auto_publish.py --cover ./cover.png --title "笔记标题" --body "正文内容"
    python xhs_auto_publish.py --cover ./cover.png --title "标题" --body "正文" --headless
    python xhs_auto_publish.py --check "标题关键词"   # 仅检查是否已发布

已验证的关键事实（2026-09-16 更新）：
- 底部有两个 <xhs-publish-btn> 自定义元素：第一个是"暂存离开"，第二个是"发布"
- 鼠标坐标点击、JS .click()、dispatchEvent 对 xhs-publish-btn 均无效
- ✅ 唯一可靠方法：Tab键切换焦点到第二个 xhs-publish-btn，然后按 Enter 键
- 正文选择器 .tiptap.ProseMirror，输入#标签会弹 Tippy 下拉需隐藏
- 成功标志：URL 含 published=true 或页面回到发布初始状态且草稿箱清空
- file input 是 hidden 元素（class="upload-input"），必须用 set_input_files 直接设置，不能等 visible
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
except ImportError:
    print("❌ 未安装 playwright，请先执行：")
    print("   pip install playwright")
    print("   playwright install chromium")
    sys.exit(1)


# ============================================================
# 配置
# ============================================================

PUBLISH_URL = "https://creator.xiaohongshu.com/publish/publish?source=official"
NOTE_MANAGER_URL = "https://creator.xiaohongshu.com/new/note-manager?source=official"
DEFAULT_TIMEOUT = 30000  # 30秒


def load_config(config_path=None):
    """加载配置文件，返回配置字典"""
    if config_path and os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    # 默认配置
    return {
        "headless": False,
        "timeout": 30000,
        "output_dir": "./output",
        "state_file": "./publish_state.json",
        "wecom": {
            "enabled": False,
            "chat_id": "",
            "cli_path": "wecom-cli"
        }
    }


# ============================================================
# 页面操作函数
# ============================================================

def wait_for_login(page, timeout=120):
    """等待用户完成登录（检测页面是否跳转到发布页或出现发布按钮）"""
    print("🔐 检测登录状态...")
    start = time.time()
    while time.time() - start < timeout:
        url = page.url
        # 如果已经在发布页且能看到发布按钮，说明已登录
        if "publish" in url:
            try:
                btn = page.query_selector("xhs-publish-btn")
                if btn:
                    print("✅ 已检测到登录状态")
                    return True
            except Exception:
                pass
        # 如果跳转到了登录页
        if "login" in url or "passport" in url:
            print("⏳ 等待用户扫码登录...（超时时间 %d 秒）" % timeout)
        time.sleep(2)
    print("⚠️ 登录等待超时")
    return False


def close_guide_popup(page):
    """关闭新手引导弹窗和遮罩层"""
    try:
        page.evaluate("""
        () => {
            document.querySelectorAll('.guide-close, .close-guide, [class*="guide"] [class*="close"]').forEach(e => {
                try { e.click(); } catch(err) {}
            });
            document.querySelectorAll('.mask, .overlay, [class*="mask"]').forEach(e => {
                e.style.display = 'none';
            });
        }
        """)
        time.sleep(0.5)
    except Exception as e:
        print("  关闭引导弹窗时出现异常（可忽略）：%s" % str(e)[:100])


def switch_to_image_tab(page):
    """切换到上传图文标签（用鼠标坐标点击，解决元素在视口外的问题）"""
    print("🔄 切换到上传图文模式...")
    try:
        # 用JS找到可见的"上传图文"元素坐标
        pos = page.evaluate("""
            () => {
                const all = document.querySelectorAll('*');
                for (const el of all) {
                    if (el.textContent && el.textContent.trim() === '上传图文' && el.children.length === 0) {
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0 && rect.x > 0 && rect.y > 0 && rect.x < 2000) {
                            return {x: rect.x + rect.width/2, y: rect.y + rect.height/2};
                        }
                    }
                }
                return null;
            }
        """)
        if pos:
            page.mouse.click(pos['x'], pos['y'])
            time.sleep(3)
            print("  ✅ 已切换到上传图文模式")
            return
    except Exception as e:
        print("  坐标点击失败：%s" % str(e)[:80])

    # 备用：JS点击
    try:
        page.evaluate("""
        () => {
            const spans = document.querySelectorAll('span, div, button');
            for (const s of spans) {
                if (s.textContent && s.textContent.includes('上传图文')) {
                    s.click();
                    return true;
                }
            }
            return false;
        }
        """)
        time.sleep(3)
    except Exception as e:
        print("  ⚠️ 切换图文标签失败：%s" % str(e)[:100])


def upload_cover(page, cover_path):
    """上传封面图"""
    cover_path = os.path.abspath(cover_path)
    if not os.path.exists(cover_path):
        raise FileNotFoundError("封面图不存在：%s" % cover_path)

    print("📤 上传封面：%s" % os.path.basename(cover_path))

    # 方法1：找 hidden 的 file input 直接上传（class="upload-input"，accept=".jpg,.jpeg,.png,.webp"）
    # 注意：这个元素是 hidden 的，不能用 wait_for_selector(visible=True)，必须直接 query_selector
    try:
        file_input = page.query_selector('input.upload-input[type="file"]')
        if not file_input:
            # 备用：找所有 file input
            file_inputs = page.query_selector_all('input[type="file"]')
            for fi in file_inputs:
                try:
                    accept = fi.get_attribute('accept') or ''
                    if 'jpg' in accept or 'png' in accept or 'image' in accept:
                        file_input = fi
                        break
                except Exception:
                    continue
        
        if file_input:
            file_input.set_input_files(cover_path)
            time.sleep(10)  # 上传需要更多时间，且上传后标题/正文元素才会出现
            # 等待标题输入框出现（上传封面后才会渲染编辑界面）
            try:
                page.wait_for_selector('input[placeholder*="标题"]', timeout=15000)
            except Exception:
                print("  ⚠️ 等待标题输入框超时，继续...")
            print("  ✅ 封面上传成功（hidden file input 方式）")
            return
    except Exception as e:
        print("  hidden file input 方式失败：%s" % str(e)[:80])

    # 方法2：点击上传区域后上传
    try:
        upload_areas = page.query_selector_all('[class*="upload"], [class*="Upload"]')
        for area in upload_areas:
            try:
                area.click()
                time.sleep(1)
                file_input = page.query_selector('input[type="file"]')
                if file_input:
                    file_input.set_input_files(cover_path)
                    time.sleep(3)
                    print("  ✅ 封面上传成功（点击区域方式）")
                    return
            except Exception:
                continue
    except Exception as e:
        print("  点击区域方式失败：%s" % str(e)[:80])

    raise Exception("封面上传失败，所有方式均无效")


def fill_title(page, title):
    """填写标题"""
    print("✏️  填写标题...")
    try:
        page.wait_for_selector('input[placeholder*="标题"]', timeout=10000)
    except PlaywrightTimeout:
        print("  ⚠️ 未找到标题输入框，尝试其他选择器...")

    # 方法1：直接 fill
    try:
        title_input = page.query_selector('input[placeholder*="标题"]')
        if title_input:
            title_input.fill("")
            title_input.fill(title)
            time.sleep(0.5)
            print("  ✅ 标题填写成功")
            return
    except Exception as e:
        print("  fill 方式失败：%s" % str(e)[:80])

    # 方法2：点击后键盘输入
    try:
        title_input = page.query_selector('input[placeholder*="标题"]')
        if title_input:
            title_input.click()
            time.sleep(0.3)
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
            page.keyboard.type(title, delay=10)
            time.sleep(0.5)
            print("  ✅ 标题填写成功（键盘方式）")
            return
    except Exception as e:
        print("  键盘方式失败：%s" % str(e)[:80])

    raise Exception("标题填写失败")


def fill_body(page, body_text):
    """填写正文（含标签）"""
    print("📝 填写正文...")
    try:
        page.wait_for_selector('.tiptap.ProseMirror', timeout=15000)
    except PlaywrightTimeout:
        print("  ⚠️ 等待编辑器超时，继续尝试...")

    editor = page.query_selector('.tiptap.ProseMirror')
    if not editor:
        raise Exception("未找到富文本编辑器 .tiptap.ProseMirror")

    # 聚焦并清空
    editor.click()
    time.sleep(0.3)
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    time.sleep(0.3)

    # 用键盘输入（最可靠）
    page.keyboard.type(body_text, delay=5)
    time.sleep(1)

    # 关闭话题下拉弹窗
    close_topic_dropdown(page)

    print("  ✅ 正文填写成功")


def close_topic_dropdown(page):
    """隐藏话题标签下拉弹窗（Tippy）"""
    try:
        page.evaluate("""
        () => {
            document.querySelectorAll('.tippy-box').forEach(e => e.style.display = 'none');
            document.querySelectorAll('[data-tippy-root]').forEach(e => e.style.display = 'none');
        }
        """)
        time.sleep(0.5)
    except Exception:
        pass


def click_publish_button(page):
    """
    点击底部红色发布按钮（核心方法：Tab定位到第二个xhs-publish-btn后按Enter）
    
    已验证：鼠标坐标点击、JS .click()、dispatchEvent 对 xhs-publish-btn 均无效。
    唯一可靠方法是用键盘Tab切换焦点到第二个xhs-publish-btn（发布按钮），然后按Enter。
    """
    print("🔴 点击发布按钮（Tab+Enter方法）...")

    # 先滚动到底部确保按钮可见
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    time.sleep(1)

    # 先点击正文编辑器获取焦点（确保Tab从可预测的位置开始）
    try:
        editor = page.query_selector('.tiptap.ProseMirror')
        if editor:
            editor.click()
            time.sleep(0.3)
    except Exception:
        pass

    # 用Tab键切换焦点，统计遇到的xhs-publish-btn数量
    # 第一个是"暂存离开"，第二个是"发布"
    xhs_btn_count = 0
    max_tabs = 30
    
    for i in range(max_tabs):
        try:
            active_tag = page.evaluate("document.activeElement.tagName")
        except Exception:
            active_tag = ""
        
        if active_tag == 'XHS-PUBLISH-BTN':
            xhs_btn_count += 1
            print("  Tab %d: 第%d个 xhs-publish-btn" % (i, xhs_btn_count))
            
            if xhs_btn_count == 2:
                print("  ✅ 焦点已在发布按钮上，按Enter触发发布！")
                page.keyboard.press("Enter")
                time.sleep(3)
                print("  ✅ 已触发发布")
                return
        
        page.keyboard.press("Tab")
        time.sleep(0.15)
    
    # 备用方案：如果Tab没找到第二个按钮，尝试直接focus第二个元素后按Enter
    print("  ⚠️ Tab方法未找到发布按钮，尝试备用方案...")
    try:
        page.evaluate("""
        () => {
            const btns = document.querySelectorAll('xhs-publish-btn');
            if (btns.length >= 2) {
                btns[1].focus();
                return true;
            }
            return false;
        }
        """)
        time.sleep(0.5)
        page.keyboard.press("Enter")
        time.sleep(3)
        print("  ✅ 备用方案已触发发布")
    except Exception as e:
        print("  ❌ 备用方案也失败：%s" % str(e)[:100])
        raise Exception("发布按钮点击失败，所有方法均无效")


def verify_publish_success(page, timeout=15):
    """验证发布成功"""
    print("🔍 验证发布结果...")
    start = time.time()
    while time.time() - start < timeout:
        url = page.url
        if 'success' in url or 'published=true' in url:
            print("  ✅ 发布成功！URL: %s" % url)
            return True
        try:
            success_text = page.evaluate("""
            () => {
                const body = document.body.innerText;
                return body.includes('发布成功') || body.includes('已发布');
            }
            """)
            if success_text:
                print("  ✅ 检测到发布成功提示")
                return True
        except Exception:
            pass
        time.sleep(1)
    print("  ⚠️ 未检测到明确的发布成功信号，请手动确认")
    return False


def check_note_published(page, title_keyword, timeout=20):
    """检查笔记是否已发布（在笔记管理页搜索标题关键词）"""
    print("🔍 检查笔记是否已发布，关键词：%s" % title_keyword)
    try:
        page.goto(NOTE_MANAGER_URL, wait_until="domcontentloaded", timeout=30000)
        time.sleep(5)

        for _ in range(3):
            page.mouse.wheel(0, 2000)
            time.sleep(1)

        page_text = page.evaluate("() => document.body.innerText")
        if title_keyword in page_text:
            print("  ✅ 已找到该笔记，确认已发布")
            return True
        else:
            print("  ❌ 未找到该笔记")
            return False
    except Exception as e:
        print("  ⚠️ 检查过程出错：%s" % str(e)[:100])
        return False


# ============================================================
# 主流程
# ============================================================

def full_publish(cover_path, title, body_text, config=None):
    """
    完整自动发布流程

    Args:
        cover_path: 封面图本地路径
        title: 笔记标题
        body_text: 正文（含标签，标签放最后）
        config: 配置字典，可包含 cdp_url（连接已登录浏览器）

    Returns:
        bool: 是否发布成功
    """
    if config is None:
        config = load_config()

    headless = config.get("headless", False)
    timeout = config.get("timeout", DEFAULT_TIMEOUT)
    cdp_url = config.get("cdp_url", None)

    with sync_playwright() as p:
        if cdp_url:
            # CDP模式：连接当前已登录的浏览器（推荐用于自动发布）
            print("🔗 连接已登录浏览器（CDP: %s）..." % cdp_url)
            browser = p.chromium.connect_over_cdp(cdp_url)
            # 使用默认context（已登录的那个）
            if len(browser.contexts) > 0:
                context = browser.contexts[0]
            else:
                context = browser.new_context(viewport={'width': 1920, 'height': 1080})

            # 查找已有的发布页，存在则复用
            page = None
            for p in context.pages:
                if "publish" in p.url:
                    page = p
                    print("  复用已有发布页")
                    break
            if not page:
                page = context.new_page()
                print("  新建发布页")

            # 设置大视口（确保元素在视口内）
            page.set_viewport_size({'width': 1920, 'height': 1080})
        else:
            # 普通模式：启动新浏览器（需要用户扫码登录）
            print("🚀 启动新浏览器（headless=%s）..." % headless)
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )
            page = context.new_page()

        page.set_default_timeout(timeout)

        try:
            print("🌐 打开发布页...")
            page.goto(PUBLISH_URL, wait_until="domcontentloaded", timeout=30000)
            time.sleep(5)

            # CDP模式下通常已登录，简化登录检测
            if cdp_url:
                print("🔐 CDP模式，假设已登录...")
            else:
                if not wait_for_login(page, timeout=120):
                    print("❌ 登录失败或超时")
                    return False

            close_guide_popup(page)
            switch_to_image_tab(page)
            upload_cover(page, cover_path)
            fill_title(page, title)
            fill_body(page, body_text)
            close_topic_dropdown(page)
            click_publish_button(page)

            success = verify_publish_success(page)

            if not headless and not cdp_url:
                print("⏳ 停留5秒供观察...")
                time.sleep(5)

            return success

        except Exception as e:
            print("❌ 发布过程出错：%s" % str(e))
            import traceback
            traceback.print_exc()
            return False
        finally:
            if cdp_url:
                # CDP模式只断开连接，不关闭浏览器
                print("🔌 断开CDP连接（浏览器保持运行）")
            else:
                browser.close()
                print("🔒 浏览器已关闭")


def main():
    parser = argparse.ArgumentParser(description="小红书图文笔记自动发布工具")
    parser.add_argument("--cover", help="封面图本地路径")
    parser.add_argument("--title", help="笔记标题")
    parser.add_argument("--body", help="正文内容（含标签）")
    parser.add_argument("--body-file", help="从文件读取正文内容")
    parser.add_argument("--check", help="仅检查笔记是否已发布，传入标题关键词")
    parser.add_argument("--config", help="配置文件路径（JSON）", default=None)
    parser.add_argument("--headless", action="store_true", help="无头模式（不显示浏览器窗口）")
    parser.add_argument("--cdp", help="连接已登录浏览器的CDP地址，如 http://127.0.0.1:9222", default=None)
    parser.add_argument("--timeout", type=int, default=30000, help="超时时间（毫秒）")

    args = parser.parse_args()

    config = load_config(args.config)
    if args.headless:
        config['headless'] = True
    if args.cdp:
        config['cdp_url'] = args.cdp
    if args.timeout:
        config['timeout'] = args.timeout

    # 模式1：仅检查是否已发布
    if args.check:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=config.get('headless', False))
            context = browser.new_context(viewport={'width': 1280, 'height': 800})
            page = context.new_page()
            page.set_default_timeout(config.get('timeout', 30000))
            try:
                page.goto(PUBLISH_URL, wait_until="domcontentloaded", timeout=30000)
                time.sleep(3)
                wait_for_login(page, timeout=120)
                result = check_note_published(page, args.check)
                sys.exit(0 if result else 1)
            finally:
                browser.close()

    # 模式2：完整发布
    if not args.cover or not args.title:
        parser.error("发布模式需要 --cover 和 --title 参数")

    body_text = args.body
    if args.body_file:
        with open(args.body_file, "r", encoding="utf-8") as f:
            body_text = f.read()
    if not body_text:
        parser.error("需要 --body 或 --body-file 参数")

    success = full_publish(args.cover, args.title, body_text, config)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
