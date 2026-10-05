#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
牵牛花数据历史回溯脚本
从指定日期开始，每天跑一遍所有表的采集
"""

import os
import sys
import time
import argparse
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import get_data_dir


# 要回溯的任务（按优先级排序）
TASKS = [
    {
        "id": 2,
        "name": "门店经营指标",
        "script": "download_store_metrics.py",
        "timeout": 180,
    },
    {
        "id": 8,
        "name": "门店商品指标明细",
        "script": "download_store_product_daily.py",
        "timeout": 180,
    },
    {
        "id": 9,
        "name": "配送履约数据",
        "script": "download_delivery.py",
        "timeout": 180,
    },
    {
        "id": 5,
        "name": "品类分析",
        "script": "download_category.py",
        "timeout": 120,
    },
    {
        "id": 6,
        "name": "门店营业时长",
        "script": "download_store_hours.py",
        "timeout": 120,
    },
    {
        "id": 3,
        "name": "门店流量",
        "script": "download_store_traffic.py",
        "timeout": 120,
    },
    {
        "id": 4,
        "name": "商品流量（三Tab）",
        "script": "download_product_traffic.py",
        "timeout": 300,
    },
    # 订单API和30天流量排名单独处理
]


def run_task_for_date(task, date_str):
    """运行单个采集任务"""
    script_path = Path(__file__).parent / task["script"]
    
    cmd = [
        sys.executable,
        str(script_path),
        "--date", date_str,
    ]
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=task["timeout"],
            cwd=str(Path(__file__).parent),
        )
        return result.returncode == 0, None
    except subprocess.TimeoutExpired:
        return False, "超时"
    except Exception as e:
        return False, str(e)


def main():
    parser = argparse.ArgumentParser(description="牵牛花数据历史回溯")
    parser.add_argument("--start", required=True, help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end", help="结束日期 YYYY-MM-DD，默认昨日")
    parser.add_argument("--sleep", type=int, default=5, help="每天任务间隔秒数")
    args = parser.parse_args()

    start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
    end_date = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else (datetime.now() - timedelta(days=1)).date()
    
    days = (end_date - start_date).days + 1
    print(f"{'='*60}")
    print(f"牵牛花数据历史回溯")
    print(f"日期范围: {start_date} ~ {end_date}")
    print(f"总天数: {days}")
    print(f"任务数: {len(TASKS)}")
    print(f"预计总耗时: 约 {days * len(TASKS) * 60 / 3600:.1f} 小时")
    print(f"{'='*60}")

    # 记录进度
    progress_file = get_data_dir() / "backfill_progress.json"
    
    total_success = 0
    total_fail = 0
    total_tasks = days * len(TASKS)
    
    current_date = start_date
    day_num = 1
    
    while current_date <= end_date:
        date_str = current_date.strftime("%Y-%m-%d")
        print(f"\n{'─'*60}")
        print(f"第 {day_num}/{days} 天: {date_str}")
        print(f"{'─'*60}")
        
        day_success = 0
        day_fail = 0
        
        for task in TASKS:
            print(f"  [{task['id']}] {task['name']}...", end=" ", flush=True)
            
            success, error = run_task_for_date(task, date_str)
            
            if success:
                print("✅")
                day_success += 1
                total_success += 1
            else:
                print(f"❌ {error}")
                day_fail += 1
                total_fail += 1
        
        print(f"  本日完成: {day_success}成功, {day_fail}失败")
        
        # 保存进度
        import json
        progress = {
            "current_date": date_str,
            "day_num": day_num,
            "total_days": days,
            "total_success": total_success,
            "total_fail": total_fail,
            "updated_at": datetime.now().isoformat(),
        }
        with open(progress_file, "w", encoding="utf-8") as f:
            json.dump(progress, f, ensure_ascii=False, indent=2)
        
        # 间隔一下，避免请求太频繁
        if current_date < end_date:
            time.sleep(args.sleep)
        
        current_date += timedelta(days=1)
        day_num += 1

    # 最终汇总
    print(f"\n{'='*60}")
    print(f"历史回溯完成！")
    print(f"总天数: {days}")
    print(f"总任务数: {total_tasks}")
    print(f"成功: {total_success}")
    print(f"失败: {total_fail}")
    print(f"成功率: {total_success/total_tasks*100:.1f}%")
    print(f"{'='*60}")
    
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
