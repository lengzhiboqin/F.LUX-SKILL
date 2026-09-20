#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
牵牛花数据采集 Skill - 主采集流程
依次完成：登录态探测 → 6条路径采集 → 核对产出 → 总表追加 → 缺失检测 → 写摘要
"""

import sys
import json
import logging
import argparse
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (
    load_config, is_configured, get_skill_dir, get_data_dir,
    get_raw_dir, get_master_dir, save_result_summary,
    print_banner, print_step, setup_logging, get_yesterday,
    DATA_PATHS, filter_flux_stores
)

# ============================================================
# 采集结果汇总
# ============================================================

class CollectResult:
    def __init__(self, data_date):
        self.data_date = data_date
        self.start_time = datetime.now().isoformat()
        self.end_time = None
        self.paths = {}  # path_id -> {status, file, records, error, duration}
        self.master_append = {}  # path_id -> {appended, skipped, errors}
        self.missing_check = {}
        self.overall_status = "running"  # running/success/partial/failed

    def add_path_result(self, path_id, status, file=None, records=0, error=None, duration=0):
        self.paths[path_id] = {
            "name": DATA_PATHS.get(path_id, f"路径{path_id}"),
            "status": status,  # success/failed/skipped
            "file": str(file) if file else None,
            "records": records,
            "error": error,
            "duration_seconds": round(duration, 2),
        }

    def finalize(self):
        self.end_time = datetime.now().isoformat()
        success_count = sum(1 for p in self.paths.values() if p["status"] == "success")
        total_count = len(self.paths)
        if success_count == total_count:
            self.overall_status = "success"
        elif success_count > 0:
            self.overall_status = "partial"
        else:
            self.overall_status = "failed"

    def to_dict(self):
        return {
            "data_date": self.data_date,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "overall_status": self.overall_status,
            "paths": self.paths,
            "master_append": self.master_append,
            "missing_check": self.missing_check,
            "summary": {
                "total_paths": len(self.paths),
                "success": sum(1 for p in self.paths.values() if p["status"] == "success"),
                "failed": sum(1 for p in self.paths.values() if p["status"] == "failed"),
                "skipped": sum(1 for p in self.paths.values() if p["status"] == "skipped"),
            }
        }

# ============================================================
# 浏览器采集模块（接口定义，实际实现需根据环境调试）
# ============================================================

class BrowserCollector:
    """浏览器采集器 - 通过 Chrome CDP 连接已登录的浏览器执行采集"""

    def __init__(self, config):
        self.config = config
        self.port = config.get("chrome_port", 9222)
        self.base_url = config.get("base_url", "https://qnh.meituan.com")
        self.cdp_url = f"http://127.0.0.1:{self.port}"

    def connect(self):
        """连接浏览器，返回是否成功"""
        import urllib.request
        try:
            url = f"{self.cdp_url}/json/version"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                logging.info(f"已连接浏览器: {data.get('Browser', 'unknown')}")
                return True
        except Exception as e:
            logging.error(f"浏览器连接失败: {e}")
            return False

    def collect_path(self, path_id, data_date, output_dir):
        """
        执行单条路径采集
        返回: (status, filepath, records, error)
        """
        import time
        start = time.time()

        try:
            if path_id == 1:
                return self._collect_orders_api(data_date, output_dir)
            elif path_id == 2:
                return self._collect_store_metrics(data_date, output_dir)
            elif path_id == 3:
                return self._collect_store_traffic(data_date, output_dir)
            elif path_id == 4:
                return self._collect_product_traffic(data_date, output_dir)
            elif path_id == 5:
                return self._collect_category_analysis(data_date, output_dir)
            elif path_id == 6:
                return self._collect_store_products(data_date, output_dir)
            else:
                return "skipped", None, 0, f"未知路径编号: {path_id}"
        except Exception as e:
            duration = time.time() - start
            logging.error(f"路径{path_id}采集异常: {e}", exc_info=True)
            return "failed", None, 0, str(e)

    # ---- 各路径采集实现（占位，需根据实际页面结构调试） ----

    def _collect_orders_api(self, data_date, output_dir):
        """路径1: 订单API采集"""
        # 实际实现：通过 CDP 在页面上下文中执行 fetch 调用 newQueryList 接口
        # 按小时切片拉取全天数据，聚合为门店×小时×SPU
        # 输出 JSON 文件
        output_file = Path(output_dir) / f"订单API_{data_date}.json"

        # TODO: 实现 API 调用逻辑
        # 参考 references/data_paths.md 路径1的参数说明

        # 临时占位：创建空文件标记
        output_file.write_text(json.dumps({"data_date": data_date, "status": "placeholder", "orders": []}, ensure_ascii=False, indent=2))
        logging.warning(f"路径1（订单API）为占位实现，需调试后启用")
        return "skipped", output_file, 0, "占位实现，待调试"

    def _collect_store_metrics(self, data_date, output_dir):
        """路径2: 门店经营指标导出"""
        # 实际实现：浏览器导航到首页，点击门店明细导出，轮询任务中心，下载文件
        output_file = Path(output_dir) / f"门店经营指标_{data_date}.csv"
        logging.warning(f"路径2（门店经营指标）为占位实现，需调试后启用")
        return "skipped", output_file, 0, "占位实现，待调试"

    def _collect_store_traffic(self, data_date, output_dir):
        """路径3: 门店流量导出"""
        output_file = Path(output_dir) / f"门店流量_{data_date}.csv"
        logging.warning(f"路径3（门店流量）为占位实现，需调试后启用")
        return "skipped", output_file, 0, "占位实现，待调试"

    def _collect_product_traffic(self, data_date, output_dir):
        """路径4: 商品流量导出（三Tab）"""
        # 需要分别导出全部/店外/店内三个Tab
        output_files = []
        for tab in ["全部", "店外", "店内"]:
            output_files.append(Path(output_dir) / f"商品流量_{tab}_{data_date}.csv")
        logging.warning(f"路径4（商品流量）为占位实现，需调试后启用")
        return "skipped", output_files[0], 0, "占位实现，待调试"

    def _collect_category_analysis(self, data_date, output_dir):
        """路径5: 品类分析导出（需先选指标）"""
        output_file = Path(output_dir) / f"品类分析_{data_date}.csv"
        logging.warning(f"路径5（品类分析）为占位实现，需调试后启用")
        return "skipped", output_file, 0, "占位实现，待调试"

    def _collect_store_products(self, data_date, output_dir):
        """路径6: 门店在售商品读取（逐店遍历）"""
        output_file = Path(output_dir) / f"门店在售商品_{data_date}.csv"
        logging.warning(f"路径6（门店在售商品）为占位实现，需调试后启用")
        return "skipped", output_file, 0, "占位实现，待调试"

# ============================================================
# 总表追加
# ============================================================

def append_to_master(raw_file, table_name, data_date):
    """将原始数据追加到累积总表"""
    import csv

    master_dir = get_master_dir()
    master_file = master_dir / f"{table_name}.csv"

    if not raw_file or not Path(raw_file).exists():
        return {"appended": 0, "skipped": 0, "errors": ["原始文件不存在"]}

    try:
        # 读取原始文件
        with open(raw_file, "r", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            fieldnames = reader.fieldnames or []

        if not rows:
            return {"appended": 0, "skipped": 0, "errors": ["原始文件为空"]}

        # 添加采集日期列
        for row in rows:
            row["_采集日期"] = data_date

        # 如果总表不存在，创建并写入表头
        if not master_file.exists():
            all_fields = fieldnames + ["_采集日期"]
            with open(master_file, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=all_fields)
                writer.writeheader()
                writer.writerows(rows)
            return {"appended": len(rows), "skipped": 0, "errors": []}

        # 总表已存在，先删除该日期的旧数据（幂等），再追加新数据
        with open(master_file, "r", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.DictReader(f)
            existing_fields = reader.fieldnames or []
            existing_rows = [r for r in reader if r.get("_采集日期") != data_date]

        # 合并字段（取并集）
        all_fields = list(dict.fromkeys(existing_fields + fieldnames + ["_采集日期"]))

        # 写入
        with open(master_file, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=all_fields)
            writer.writeheader()
            writer.writerows(existing_rows)
            writer.writerows(rows)

        return {
            "appended": len(rows),
            "skipped": len(existing_rows),
            "errors": [],
            "total_after": len(existing_rows) + len(rows)
        }

    except Exception as e:
        logging.error(f"总表追加失败 [{table_name}]: {e}", exc_info=True)
        return {"appended": 0, "skipped": 0, "errors": [str(e)]}

# ============================================================
# 缺失检测
# ============================================================

def check_missing_data(data_date):
    """检测总表中是否有缺失日期"""
    master_dir = get_master_dir()
    result = {}

    for table_file in master_dir.glob("*.csv"):
        table_name = table_file.stem
        try:
            import csv
            with open(table_file, "r", encoding="utf-8-sig", errors="replace") as f:
                reader = csv.DictReader(f)
                dates = set()
                for row in reader:
                    d = row.get("_采集日期", "")
                    if d:
                        dates.add(d)

            # 检查最近30天是否有缺失
            from datetime import timedelta
            today = datetime.now().date()
            expected_dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(30)]
            missing = [d for d in expected_dates if d not in dates]

            result[table_name] = {
                "total_dates": len(dates),
                "latest_date": max(dates) if dates else None,
                "missing_last_30d": missing[:10],  # 只显示前10个
                "missing_count": len(missing),
            }
        except Exception as e:
            result[table_name] = {"error": str(e)}

    return result

# ============================================================
# 主流程
# ============================================================

def run_collection(data_date, dry_run=False, backfill=False, paths=None):
    """执行采集主流程"""
    config = load_config()
    if not config:
        print("❌ 未找到配置文件，请先运行安装向导")
        print(f"   bash {get_skill_dir()}/scripts/setup.sh")
        return 3

    result = CollectResult(data_date)
    raw_dir = get_raw_dir(data_date)

    print_banner(f"牵牛花数据采集 · {data_date}")
    print(f"数据目录: {get_data_dir()}")
    print(f"原始数据: {raw_dir}")
    if dry_run:
        print("⚠️  试运行模式（dry-run）：不执行实际采集")
    print()

    # Step 1: 登录态检查
    print_step(1, 4, "登录态检查")
    try:
        from check_login import run_check
        is_logged_in, msg = run_check(verbose=True)
        if not is_logged_in:
            print(f"\n❌ {msg}")
            print("请先登录牵牛花中台后重新运行。")
            result.overall_status = "failed"
            save_result_summary(result.to_dict())
            return 2
        print(f"\n✅ {msg}")
    except Exception as e:
        print(f"⚠️  登录态检查异常: {e}，将继续执行（可能采集失败）")

    if dry_run:
        print("\n✅ 试运行完成（未执行实际采集）")
        result.overall_status = "success"
        save_result_summary(result.to_dict())
        return 0

    # Step 2: 连接浏览器
    print_step(2, 4, "连接浏览器")
    collector = BrowserCollector(config)
    if not collector.connect():
        print("❌ 浏览器连接失败，请确认 Chrome 已启动并开启远程调试端口")
        result.overall_status = "failed"
        save_result_summary(result.to_dict())
        return 2
    print("✅ 浏览器已连接")

    # Step 3: 执行 6 条路径采集
    print_step(3, 4, "执行数据采集")
    path_ids = paths if paths else [1, 2, 3, 4, 5, 6]

    for path_id in path_ids:
        name = DATA_PATHS.get(path_id, f"路径{path_id}")
        print(f"\n  [{path_id}/6] {name}")

        status, filepath, records, error = collector.collect_path(path_id, data_date, raw_dir)

        if status == "success":
            print(f"    ✅ 成功，{records} 条记录 → {filepath}")
        elif status == "skipped":
            print(f"    ⏭️  跳过: {error}")
        else:
            print(f"    ❌ 失败: {error}")

        result.add_path_result(path_id, status, filepath, records, error)

    # Step 4: 总表追加 + 缺失检测
    print_step(4, 4, "总表追加与缺失检测")

    # 路径与总表名的映射
    path_table_map = {
        1: "订单明细",
        2: "门店经营指标",
        3: "门店流量",
        4: "商品流量",
        5: "品类分析",
        6: "门店在售商品",
    }

    for path_id, path_result in result.paths.items():
        if path_result["status"] == "success" and path_result["file"]:
            table_name = path_table_map.get(int(path_id), f"路径{path_id}")
            append_result = append_to_master(path_result["file"], table_name, data_date)
            result.master_append[path_id] = append_result
            if append_result["errors"]:
                print(f"  ⚠️  {table_name} 追加异常: {append_result['errors']}")
            else:
                print(f"  ✅ {table_name}: 追加 {append_result['appended']} 条")

    # 缺失检测
    print("\n  缺失数据检测:")
    missing = check_missing_data(data_date)
    result.missing_check = missing
    for table, info in missing.items():
        if "error" in info:
            print(f"    ⚠️  {table}: 检测失败 - {info['error']}")
        elif info.get("missing_count", 0) > 0:
            print(f"    ⚠️  {table}: 近30天缺失 {info['missing_count']} 天，最新: {info['latest_date']}")
        else:
            print(f"    ✅ {table}: 数据完整，最新: {info['latest_date']}")

    # 完成
    result.finalize()
    summary_file = save_result_summary(result.to_dict())

    print_banner("采集完成")
    summary = result.to_dict()["summary"]
    print(f"  总路径数: {summary['total_paths']}")
    print(f"  成功: {summary['success']}")
    print(f"  失败: {summary['failed']}")
    print(f"  跳过: {summary['skipped']}")
    print(f"  整体状态: {result.overall_status}")
    print(f"  结果摘要: {summary_file}")
    print()

    if result.overall_status == "success":
        return 0
    elif result.overall_status == "partial":
        return 1
    else:
        return 2

# ============================================================
# 命令行入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="牵牛花数据采集主流程")
    parser.add_argument("--date", help="数据日期（YYYY-MM-DD），默认昨日")
    parser.add_argument("--dry-run", action="store_true", help="试运行，不执行实际采集")
    parser.add_argument("--backfill", action="store_true", help="回溯历史数据（按配置的 history_days）")
    parser.add_argument("--paths", type=int, nargs="+", help="只采集指定路径（1-6）")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细输出")
    args = parser.parse_args()

    setup_logging(level=logging.DEBUG if args.verbose else logging.INFO)

    # 回溯模式
    if args.backfill:
        config = load_config()
        if not config:
            print("❌ 未配置，请先运行安装向导")
            return 3
        days = config.get("history_days", 7)
        from datetime import timedelta
        today = datetime.now().date()
        dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days, 0, -1)]
        print(f"将回溯 {days} 天数据: {dates[0]} ~ {dates[-1]}")
        if not __import__("common").confirm("确认开始回溯？", default=True):
            return 0
        for d in dates:
            print(f"\n{'='*60}")
            print(f"回溯日期: {d}")
            print(f"{'='*60}")
            run_collection(d, paths=args.paths)
        return 0

    # 正常采集
    data_date = args.date or get_yesterday()
    exit_code = run_collection(data_date, dry_run=args.dry_run, paths=args.paths)
    sys.exit(exit_code)

if __name__ == "__main__":
    main()
