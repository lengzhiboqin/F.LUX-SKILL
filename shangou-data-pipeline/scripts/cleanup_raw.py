#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""整合完成后清理临时原始数据。

策略：
1. 删除已成功并入总表的日期目录（raw/<日期>/）
2. 保留最近 N 天的原始数据作为缓冲（默认0，即全部清理）
3. 只清理已成功追加到总表的日期，失败的保留以便重跑

用法：
  python3 cleanup_raw.py --raw <raw_dir> --master <master_dir> [--keep-days 0]
  python3 cleanup_raw.py --config <config.json>  # 从配置读取路径
"""
import argparse, csv, glob, json, os, re, shutil, sys
from datetime import datetime, timedelta


def get_master_dates(master_dir):
    """从总表 by_table/ 中提取所有已采集日期。"""
    bt = os.path.join(master_dir, "by_table")
    dates = set()
    if not os.path.isdir(bt):
        return dates
    for f in glob.glob(os.path.join(bt, "*.csv")):
        if os.path.basename(f).startswith("_"):
            continue
        try:
            with open(f, encoding="utf-8-sig") as fh:
                reader = csv.reader(fh)
                header = next(reader, [])
                # _采集日期 在最后一列
                for row in reader:
                    if row and row[-1]:
                        d = row[-1][:10]
                        if re.match(r"\d{4}-\d{2}-\d{2}", d):
                            dates.add(d)
        except Exception:
            continue
    return dates


def main():
    ap = argparse.ArgumentParser(description="清理已整合的原始数据")
    ap.add_argument("--raw", help="原始数据目录")
    ap.add_argument("--master", help="总表目录")
    ap.add_argument("--keep-days", type=int, default=0,
                    help="保留最近N天的原始数据（默认0=全部清理已整合的）")
    ap.add_argument("--config", help="配置文件路径（从中读取raw_dir和master_dir）")
    ap.add_argument("--dry-run", action="store_true", help="只打印不删除")
    args = ap.parse_args()

    # 从配置读取
    if args.config and os.path.exists(args.config):
        cfg = json.loads(open(args.config, encoding="utf-8").read())
        if not args.raw:
            args.raw = cfg.get("raw_dir", "")
        if not args.master:
            args.master = cfg.get("master_dir", "")

    if not args.raw or not args.master:
        print("错误：请指定 --raw 和 --master，或通过 --config 指定配置文件", file=sys.stderr)
        sys.exit(1)

    if not os.path.isdir(args.raw):
        print(f"原始数据目录不存在: {args.raw}")
        sys.exit(0)

    master_dates = get_master_dates(args.master)
    if not master_dates:
        print("总表无数据，不清理原始数据。")
        sys.exit(0)

    # 计算保留截止日期
    if args.keep_days > 0:
        cutoff = (datetime.now() - timedelta(days=args.keep_days)).strftime("%Y-%m-%d")
    else:
        cutoff = ""

    # 扫描 raw/ 下的日期目录
    removed = []
    kept = []
    for d in sorted(glob.glob(os.path.join(args.raw, "*"))):
        if not os.path.isdir(d):
            continue
        day = os.path.basename(d)
        if not re.match(r"\d{4}-\d{2}-\d{2}$", day):
            continue

        # 只清理已并入总表的日期
        if day not in master_dates:
            kept.append(f"{day}(未入总表)")
            continue

        # 保留最近 N 天
        if cutoff and day >= cutoff:
            kept.append(f"{day}(保留缓冲)")
            continue

        if args.dry_run:
            removed.append(f"{day}(dry-run)")
        else:
            shutil.rmtree(d, ignore_errors=True)
            removed.append(day)

    print(f"===== 原始数据清理 =====")
    if removed:
        print(f"  已清理 {len(removed)} 个日期目录: {', '.join(removed)}")
    else:
        print("  无需要清理的目录")
    if kept:
        print(f"  保留 {len(kept)} 个目录: {', '.join(kept)}")

    # 如果 raw/ 目录已空，也清理掉空目录
    remaining = [d for d in os.listdir(args.raw)
                 if os.path.isdir(os.path.join(args.raw, d))]
    if not remaining and not args.dry_run:
        # raw 目录本身保留，只删空内容
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
