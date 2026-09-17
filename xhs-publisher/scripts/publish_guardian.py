#!/usr/bin/env python3
"""
小红书发布守护进程（技能内置定时兜底）
- 内容生成后由主流程用 nohup 后台启动
- 读取 publish_state.json，计时5分钟/10分钟
- 5分钟未发布 → 企微催促提醒（只发一次）
- 10分钟未发布 → 自动调用发布脚本（CDP连接已登录浏览器）
- 用户确认发布后删除状态文件 → 守护进程自动退出

使用方式：
    nohup python3 publish_guardian.py > /tmp/publish_guardian.log 2>&1 &
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

# ============================================================
# 配置
# ============================================================
STATE_FILE = "/home/user/.doubao/agent_mode/workspace/.user_skills/xhs-publisher/output/publish_state.json"
PUBLISH_SCRIPT = "/home/user/.doubao/agent_mode/workspace/.user_skills/xhs-publisher/scripts/xhs_auto_publish.py"
WECOM_CLI = "wecom-cli"
CHAT_ID = "woeC4LQgAAk8Ci2f7Xrp32fZ6x5R4YHA"
CDP_URL = "http://127.0.0.1:9222"  # 连接已登录的浏览器

CHECK_INTERVAL = 30  # 每30秒检查一次状态文件
REMINDER_TIME = 5 * 60  # 5分钟后发催促提醒（只发一次）
AUTO_PUBLISH_TIME = 10 * 60  # 10分钟后自动发布


def log(msg):
    """打印日志（带时间戳）"""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def read_state():
    """读取状态文件，不存在则返回None"""
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log(f"读取状态文件失败: {e}")
        return None


def send_wecom(content):
    """通过企微发送消息"""
    try:
        msg = {
            "chat_id": CHAT_ID,
            "msg_type": "markdown",
            "markdown": {"content": content}
        }
        result = subprocess.run(
            [WECOM_CLI, "message", "aibot", "send", "--json", json.dumps(msg, ensure_ascii=False)],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            log("企微消息发送成功")
            return True
        else:
            log(f"企微消息发送失败: {result.stderr[:200]}")
            return False
    except Exception as e:
        log(f"企微消息发送异常: {e}")
        return False


def auto_publish(state):
    """自动执行发布（CDP连接已登录浏览器）"""
    issue = state.get("issue", "?")
    title = state.get("title", "未命名")
    cover_path = state.get("cover_path", "")
    body_path = state.get("body_path", "")

    if not os.path.exists(cover_path):
        log(f"封面图不存在: {cover_path}")
        return False
    if not os.path.exists(body_path):
        log(f"正文文件不存在: {body_path}")
        return False

    log(f"开始自动发布第{issue}期: {title}")
    log(f"封面: {cover_path}")
    log(f"正文: {body_path}")
    log(f"CDP: {CDP_URL}")

    try:
        result = subprocess.run(
            [
                sys.executable, PUBLISH_SCRIPT,
                "--cover", cover_path,
                "--title", title,
                "--body-file", body_path,
                "--cdp", CDP_URL
            ],
            capture_output=True, text=True, timeout=300
        )
        log(f"发布脚本退出码: {result.returncode}")
        if result.stdout:
            log(f"STDOUT: {result.stdout[-800:]}")
        if result.stderr:
            log(f"STDERR: {result.stderr[-800:]}")

        if result.returncode == 0:
            send_wecom(f"老大，第{issue}期「{title}」已自动发布成功。")
            return True
        else:
            send_wecom(f"老大，第{issue}期「{title}」自动发布失败，请手动检查。")
            return False
    except subprocess.TimeoutExpired:
        log("发布脚本超时（5分钟）")
        send_wecom(f"老大，第{issue}期「{title}」自动发布超时，请手动检查。")
        return False
    except Exception as e:
        log(f"自动发布异常: {e}")
        return False


def main():
    log("=" * 50)
    log("小红书发布守护进程启动")
    log(f"状态文件: {STATE_FILE}")
    log(f"提醒时间: {REMINDER_TIME//60}分钟")
    log(f"自动发布时间: {AUTO_PUBLISH_TIME//60}分钟")
    log(f"CDP地址: {CDP_URL}")
    log("=" * 50)

    # 读取初始状态
    state = read_state()
    if not state:
        log("状态文件不存在，守护进程退出")
        return

    issue = state.get("issue", "?")
    title = state.get("title", "未命名")
    start_time = time.time()
    reminder_sent = False
    published = False

    log(f"监控第{issue}期: {title}")

    while True:
        elapsed = time.time() - start_time

        # 检查状态文件是否还存在（用户确认发布后会删除）
        current_state = read_state()
        if current_state is None:
            log("状态文件已被删除（用户已确认发布），守护进程退出")
            return

        # 检查期数是否变化（新的一期开始了）
        if current_state.get("issue") != issue:
            log(f"期数已变化（当前第{current_state.get('issue')}期），守护进程退出")
            return

        # 5分钟催促提醒（只发一次）
        if not reminder_sent and elapsed >= REMINDER_TIME:
            log(f"已过{REMINDER_TIME//60}分钟，发送催促提醒")
            send_wecom(f"老大，第{issue}期「{title}」请尽快确认发布，未确认将在5分钟后自动发布。")
            reminder_sent = True

        # 10分钟自动发布
        if not published and elapsed >= AUTO_PUBLISH_TIME:
            log(f"已过{AUTO_PUBLISH_TIME//60}分钟，开始自动发布")
            success = auto_publish(current_state)
            published = True
            if success:
                log("自动发布成功，守护进程退出")
            else:
                log("自动发布失败，守护进程退出")
            return

        # 每30秒检查一次
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
