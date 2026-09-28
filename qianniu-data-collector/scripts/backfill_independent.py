#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
牵牛花历史回溯 - 独立脚本版
每天依次调用7个独立下载脚本，每个脚本自己调日期
优点：稳定，数据正确
缺点：慢
"""

import os
import sys
import time
import subprocess
import argparse
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import get_raw_dir

SCRIPTS = Path(__file__).parent

# 7个下载脚本（按顺序）
DOWNLOAD_SCRIPTS = [
    "download_store_metrics.py",      # 1. 门店经营指标
    "download_store_traffic.py",     # 2. 门店流量
    "download_product_traffic.py",   # 3. 商品流量（三Tab）
    "download_category.py",          # 4. 品类分析
    "download_store_hours.py",       # 5. 门店营业时长
    "download_store_product_daily.py", # 6. 门店商品指标明细
    "download_delivery.py",          # 7. 配送履约数据
]

def run_script(script_name, date_str):
    """运行单个下载脚本"""
    script_path = SCRIPTS / script_name
    out_dir = get_raw_dir(date_str)
    
    print(f"    运行 {script_name}...")
    
    cmd = [
        sys.executable,
        str(script_path),
        "--date", date_str,
        "--out", str(out_dir)
    ]
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
            cwd=str(SCRIPTS.parent)
        )
        
        if result.returncode == 0:
            print(f"    ✅ {script_name} 成功")
            return True
        else:
            print(f"    ❌ {script_name} 失败: {result.stderr[-200:]}")
            return False
    except subprocess.TimeoutExpired:
        print(f"    ⏰ {script_name} 超时")
        return False
    except Exception as e:
        print(f"    ❌ {script_name} 异常: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="牵牛花历史回溯（独立脚本版）")
    parser.add_argument("--start", required=True, help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD")
    args = parser.parse_args()
    
    start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
    end_date = datetime.strptime(args.end, "%Y-%m-%d").date()
    
    days = (end_date - start_date).days + 1
    
    print(f"{'='*60}")
    print(f"牵牛花历史回溯（独立脚本版）")
    print(f"日期范围: {args.start} ~ {args.end}")
    print(f"总天数: {days}")
    print(f"每天 {len(DOWNLOAD_SCRIPTS)} 个脚本")
    print(f"{'='*60}\n")
    
    current_date = start_date
    day_num = 1
    
    while current_date <= end_date:
        date_str = current_date.strftime("%Y-%m-%d")
        out_dir = get_raw_dir(date_str)
        
        # 检查是否已完成（有至少5个文件就算完成）
        if out_dir.exists() and len(list(out_dir.glob("*.xlsx"))) >= 5:
            print(f"第 {day_num}/{days} 天: {date_str} - ⏭️ 已完成，跳过")
            current_date += timedelta(days=1)
            day_num += 1
            continue
        
        print(f"\n{'─'*60}")
        print(f"第 {day_num}/{days} 天: {date_str}")
        print(f"{'─'*60}")
        
        success_count = 0
        fail_count = 0
        
        for i, script_name in enumerate(DOWNLOAD_SCRIPTS, 1):
            print(f"\n  [{i}/{len(DOWNLOAD_SCRIPTS)}]", end="")
            if run_script(script_name, date_str):
                success_count += 1
            else:
                fail_count += 1
        
        print(f"\n  本日汇总: {success_count}成功, {fail_count}失败")
        
        current_date += timedelta(days=1)
        day_num += 1
    
    print(f"\n{'='*60}")
    print(f"全部完成！")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()
