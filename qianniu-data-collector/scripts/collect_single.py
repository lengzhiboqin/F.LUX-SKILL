#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
牵牛花数据采集 Skill - 单表补采
当某条路径采集失败时，用此脚本单独重试该路径
"""

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (
    load_config, is_configured, get_skill_dir, get_yesterday,
    print_banner, setup_logging, DATA_PATHS
)
from run_collector import BrowserCollector, append_to_master, get_raw_dir

def main():
    parser = argparse.ArgumentParser(description="牵牛花单条数据路径补采")
    parser.add_argument("--path", type=int, required=True, choices=[1,2,3,4,5,6],
                        help="路径编号 (1=订单API, 2=门店经营指标, 3=门店流量, 4=商品流量, 5=品类分析, 6=门店在售商品)")
    parser.add_argument("--date", help="数据日期（YYYY-MM-DD），默认昨日")
    parser.add_argument("--out", help="输出目录，默认 raw/<日期>/")
    parser.add_argument("--no-append", action="store_true", help="不追加到总表，只下载原始文件")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细输出")
    args = parser.parse_args()

    setup_logging(level=10 if args.verbose else 20)

    data_date = args.date or get_yesterday()
    path_name = DATA_PATHS.get(args.path, f"路径{args.path}")

    print_banner(f"单表补采 · 路径{args.path} {path_name} · {data_date}")

    if not is_configured():
        print("❌ 未找到配置文件，请先运行安装向导")
        print(f"   bash {get_skill_dir()}/scripts/setup.sh")
        sys.exit(3)

    config = load_config()
    output_dir = args.out or str(get_raw_dir(data_date))
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # 连接浏览器
    print("连接浏览器...")
    collector = BrowserCollector(config)
    if not collector.connect():
        print("❌ 浏览器连接失败")
        sys.exit(2)
    print("✅ 浏览器已连接\n")

    # 执行采集
    print(f"采集路径 {args.path}: {path_name}")
    status, filepath, records, error = collector.collect_path(args.path, data_date, output_dir)

    if status == "success":
        print(f"\n✅ 采集成功，{records} 条记录")
        print(f"   文件: {filepath}")

        # 追加到总表
        if not args.no_append and filepath:
            path_table_map = {1:"订单明细", 2:"门店经营指标", 3:"门店流量",
                               4:"商品流量", 5:"品类分析", 6:"门店在售商品"}
            table_name = path_table_map.get(args.path, f"路径{args.path}")
            print(f"\n追加到总表: {table_name}")
            result = append_to_master(filepath, table_name, data_date)
            if result["errors"]:
                print(f"   ⚠️  追加异常: {result['errors']}")
            else:
                print(f"   ✅ 追加 {result['appended']} 条")
    elif status == "skipped":
        print(f"\n⏭️  跳过: {error}")
    else:
        print(f"\n❌ 采集失败: {error}")
        sys.exit(1)

    print("\n完成。")

if __name__ == "__main__":
    main()
