#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
牵牛花数据历史回溯 - 高效版
优化：只调一次起始日期，每天点一次后一日
"""

import os
import sys
import time
import json
import argparse
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from qn_cdp import QianniuCDP
from common import get_raw_dir, get_data_dir


def get_current_date(qn):
    """获取当前日期"""
    result = qn.js('''
    (() => {
        const inputs = document.querySelectorAll('input');
        for (const input of inputs) {
            if (input.value && input.value.match(/^202\\d-\\d{2}-\\d{2}$/)) {
                return input.value;
            }
        }
        return 'unknown';
    })()
    ''')
    return result


def click_prev_day(qn):
    """点击前一日"""
    qn.js('''
    (() => {
        const all = document.querySelectorAll('*');
        for (const el of all) {
            if (el.children.length === 0 && el.textContent.trim() === '前一日') {
                const rect = el.getBoundingClientRect();
                if (rect.width > 0) {
                    el.click();
                    return 'clicked';
                }
            }
        }
        return 'not_found';
    })()
    ''')
    time.sleep(1.5)


def set_date_to(qn, target_date):
    """把日期设置到目标日期"""
    print(f"  调整日期到 {target_date}...")
    
    # 先获取当前日期
    current = get_current_date(qn)
    print(f"  当前日期: {current}")
    
    # 如果当前日期就是目标，直接返回
    if current == target_date:
        print("  日期已正确")
        return True
    
    # 计算需要点多少次前一日
    days_diff = (datetime.strptime(current, "%Y-%m-%d") - datetime.strptime(target_date, "%Y-%m-%d")).days
    print(f"  需要点前一日 {days_diff} 次")
    
    # 连续点击前一日，每次验证
    for i in range(abs(days_diff) + 10):  # 多给10次容错
        current = get_current_date(qn)
        if current == target_date:
            print(f"  ✅ 已到目标日期: {current}")
            return True
        
        click_prev_day(qn)
    
    # 最后验证
    current = get_current_date(qn)
    print(f"  最终日期: {current}")
    return current == target_date


def click_next_day(qn):
    """点击后一日"""
    qn.js('''
    (() => {
        const all = document.querySelectorAll('*');
        for (const el of all) {
            if (el.children.length === 0 && el.textContent.trim() === '后一日') {
                const rect = el.getBoundingClientRect();
                if (rect.width > 0) {
                    el.click();
                    return 'clicked';
                }
            }
        }
        return 'not_found';
    })()
    ''')
    time.sleep(2)
    
    # 验证日期
    result = qn.js('''
    (() => {
        const inputs = document.querySelectorAll('input');
        for (const input of inputs) {
            if (input.value && input.value.match(/^202\\d-\\d{2}-\\d{2}$/)) {
                return input.value;
            }
        }
        return 'unknown';
    })()
    ''')
    return result


def run_task_for_date(task_name, script_name, date_str, out_dir, timeout=180):
    """运行单个采集任务"""
    script_path = Path(__file__).parent / script_name
    
    cmd = [
        sys.executable,
        str(script_path),
        "--date", date_str,
        "--out", str(out_dir),
    ]
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(Path(__file__).parent),
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False
    except Exception as e:
        return False


# 要跑的表
TASKS = [
    {"name": "门店经营指标", "script": "download_store_metrics.py", "timeout": 120},
    {"name": "门店流量", "script": "download_store_traffic.py", "timeout": 120},
    {"name": "商品流量", "script": "download_product_traffic.py", "timeout": 240},
    {"name": "品类分析", "script": "download_category.py", "timeout": 120},
    {"name": "门店营业时长", "script": "download_store_hours.py", "timeout": 120},
    {"name": "门店商品指标明细", "script": "download_store_product_daily.py", "timeout": 180},
    {"name": "配送履约数据", "script": "download_delivery.py", "timeout": 180},
]


def main():
    parser = argparse.ArgumentParser(description="牵牛花数据历史回溯 - 高效版")
    parser.add_argument("--start", required=True, help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end", help="结束日期 YYYY-MM-DD，默认昨日")
    args = parser.parse_args()

    start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
    end_date = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else (datetime.now() - timedelta(days=1)).date()
    
    days = (end_date - start_date).days + 1
    print(f"{'='*60}")
    print(f"牵牛花数据历史回溯（高效版）")
    print(f"日期范围: {start_date} ~ {end_date}")
    print(f"总天数: {days}")
    print(f"表数: {len(TASKS)}")
    print(f"预计耗时: 约 {days * len(TASKS) * 30 / 3600:.1f} 小时")
    print(f"{'='*60}")

    # 第一步：连接CDP，把日期调到起始日
    print("\n第一步：调整起始日期...")
    with QianniuCDP() as qn:
        print(f"已连接: {qn.page_url}")
        
        # 导航到首页
        qn.goto("#/data/home/new", settle=5)
        time.sleep(3)
        
        # 设置起始日期
        if not set_date_to(qn, args.start):
            print("❌ 日期设置失败，退出")
            return 1
    
    # 第二步：逐日采集
    current_date = start_date
    day_num = 1
    
    while current_date <= end_date:
        date_str = current_date.strftime("%Y-%m-%d")
        out_dir = get_raw_dir(date_str)
        
        print(f"\n{'─'*60}")
        print(f"第 {day_num}/{days} 天: {date_str}")
        print(f"{'─'*60}")
        
        # 跑当天的所有表
        for task in TASKS:
            print(f"  {task['name']}...", end=" ", flush=True)
            success = run_task_for_date(
                task["name"], 
                task["script"], 
                date_str, 
                out_dir,
                task["timeout"]
            )
            if success:
                print("✅")
            else:
                print("❌")
        
        # 点后一日到下一天
        if current_date < end_date:
            print(f"\n  点后一日到下一天...")
            with QianniuCDP() as qn:
                qn.goto("#/data/home/new", settle=3)
                next_date = click_next_day(qn)
                print(f"  下一天日期: {next_date}")
        
        current_date += timedelta(days=1)
        day_num += 1

    print(f"\n{'='*60}")
    print(f"历史回溯完成！")
    print(f"总天数: {days}")
    print(f"{'='*60}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
