#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
牵牛花数据采集 - 主调度脚本
依次调用所有下载脚本，输出汇总报告
"""

import os
import sys
import time
import argparse
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import get_raw_dir, get_data_dir


# 采集任务配置
TASKS = [
    {
        "id": 1,
        "name": "订单API",
        "script": "download_orders.py",
        "required": True,
        "timeout": 300,
    },
    {
        "id": 2,
        "name": "门店经营指标",
        "script": "download_store_metrics.py",
        "required": True,
        "timeout": 180,
    },
    {
        "id": 3,
        "name": "门店流量",
        "script": "download_store_traffic.py",
        "required": True,
        "timeout": 120,
    },
    {
        "id": 4,
        "name": "商品流量（三Tab）",
        "script": "download_product_traffic.py",
        "required": True,
        "timeout": 300,
    },
    {
        "id": 5,
        "name": "品类分析",
        "script": "download_category.py",
        "required": True,
        "timeout": 120,
    },
    {
        "id": 6,
        "name": "门店营业时长",
        "script": "download_store_hours.py",
        "required": True,
        "timeout": 120,
    },
    {
        "id": 7,
        "name": "总部商品30天流量排名",
        "script": "download_hq_product_traffic_30d.py",
        "required": False,  # 30天滚动，不需要每天跑
        "timeout": 180,
    },
    {
        "id": 8,
        "name": "门店商品指标明细",
        "script": "download_store_product_daily.py",
        "required": True,
        "timeout": 180,
    },
    {
        "id": 9,
        "name": "配送履约数据",
        "script": "download_delivery.py",
        "required": True,
        "timeout": 180,
    },
]


def run_task(task, date_str, out_dir):
    """运行单个采集任务"""
    script_path = Path(__file__).parent / task["script"]
    
    print(f"\n{'─'*60}")
    print(f"[{task['id']}/{len(TASKS)}] {task['name']}")
    print(f"{'─'*60}")
    
    cmd = [
        sys.executable,
        str(script_path),
        "--date", date_str,
        "--out", str(out_dir),
    ]
    
    t0 = time.time()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=task["timeout"],
            cwd=str(Path(__file__).parent),
        )
        elapsed = time.time() - t0
        
        # 输出脚本的最后几行
        output = result.stdout.strip().split("\n")
        for line in output[-5:]:
            print(f"  {line}")
        
        if result.returncode == 0:
            print(f"  ✅ 成功，耗时 {elapsed:.1f} 秒")
            return {"success": True, "elapsed": elapsed, "error": None}
        else:
            error_msg = result.stderr.strip()[-200:] if result.stderr else "未知错误"
            print(f"  ❌ 失败，耗时 {elapsed:.1f} 秒")
            print(f"     错误: {error_msg}")
            return {"success": False, "elapsed": elapsed, "error": error_msg}
    
    except subprocess.TimeoutExpired:
        elapsed = time.time() - t0
        print(f"  ⏰ 超时（{task['timeout']}秒）")
        return {"success": False, "elapsed": elapsed, "error": "超时"}
    
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  ❌ 异常: {e}")
        return {"success": False, "elapsed": elapsed, "error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="牵牛花数据一键采集")
    parser.add_argument("--date", help="数据日期 YYYY-MM-DD，默认昨日")
    parser.add_argument("--skip", type=int, nargs="*", help="跳过的任务ID")
    parser.add_argument("--only", type=int, nargs="*", help="只运行指定的任务ID")
    parser.add_argument("--include-optional", action="store_true", help="包含非必须任务")
    args = parser.parse_args()

    date_str = args.date or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    out_dir = get_raw_dir(date_str)

    print(f"{'='*60}")
    print(f"牵牛花数据采集 - 主调度")
    print(f"日期: {date_str}")
    print(f"输出目录: {out_dir}")
    print(f"{'='*60}")

    # 筛选要运行的任务
    tasks_to_run = []
    for task in TASKS:
        # 非必须任务默认跳过
        if not task["required"] and not args.include_optional:
            continue
        # 指定了only，只跑指定的
        if args.only and task["id"] not in args.only:
            continue
        # 指定了skip，跳过指定的
        if args.skip and task["id"] in args.skip:
            continue
        tasks_to_run.append(task)

    print(f"\n本次运行 {len(tasks_to_run)} 个任务:")
    for task in tasks_to_run:
        print(f"  {task['id']}. {task['name']}")

    # 依次运行任务
    results = []
    total_start = time.time()
    
    for task in tasks_to_run:
        result = run_task(task, date_str, out_dir)
        results.append({
            "id": task["id"],
            "name": task["name"],
            **result,
        })

    # 汇总报告
    total_elapsed = time.time() - total_start
    success_count = sum(1 for r in results if r["success"])
    fail_count = sum(1 for r in results if not r["success"])

    print(f"\n{'='*60}")
    print(f"采集完成汇总")
    print(f"{'='*60}")
    print(f"日期: {date_str}")
    print(f"总任务数: {len(tasks_to_run)}")
    print(f"成功: {success_count}")
    print(f"失败: {fail_count}")
    print(f"总耗时: {total_elapsed:.1f} 秒")
    print()

    if fail_count > 0:
        print("失败任务:")
        for r in results:
            if not r["success"]:
                print(f"  ❌ {r['id']}. {r['name']}: {r['error']}")
        print()

    # 保存结果摘要
    summary = {
        "date": date_str,
        "run_time": datetime.now().isoformat(),
        "total_tasks": len(tasks_to_run),
        "success_count": success_count,
        "fail_count": fail_count,
        "total_elapsed": total_elapsed,
        "tasks": results,
    }
    
    summary_file = get_data_dir() / "master" / "last_collect.json"
    summary_file.parent.mkdir(parents=True, exist_ok=True)
    import json
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"结果摘要已保存: {summary_file}")

    # 返回退出码
    if fail_count == 0:
        return 0
    else:
        return 1


if __name__ == "__main__":
    sys.exit(main())
