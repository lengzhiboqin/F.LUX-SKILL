# -*- coding: utf-8 -*-
"""核对采集技能产出物：检查指定日期的8张表是否存在、文件大小是否正常、修改时间是否新鲜。

用法：
  python3 check_files.py --raw <raw_dir> [--date YYYY-MM-DD]
  不指定日期时检查最近一个日期目录。
"""
import argparse, glob, json, os, re, sys
from datetime import datetime

TABLE_KEYS = ["门店财务明细", "门店成交明细", "商品数据", "问题订单数据",
              "流量明细(新)", "流量渠道明细(新)", "评价数据", "售后订单数据"]


def find_latest_date(raw_dir):
    dirs = [d for d in glob.glob(os.path.join(raw_dir, "*"))
            if os.path.isdir(d) and re.match(r"\d{4}-\d{2}-\d{2}$", os.path.basename(d))]
    if not dirs:
        return None
    return sorted(dirs)[-1]


def check_date(raw_dir, date_str):
    date_dir = os.path.join(raw_dir, date_str)
    result = {"date": date_str, "date_dir": date_dir, "exists": os.path.isdir(date_dir),
              "tables": {}, "missing": [], "stale": [], "ok": []}

    if not result["exists"]:
        result["missing"] = list(TABLE_KEYS)
        return result

    now = datetime.now().timestamp()
    for tab in TABLE_KEYS:
        pattern = os.path.join(date_dir, f"{tab}_*.csv")
        files = glob.glob(pattern)
        if not files:
            result["missing"].append(tab)
            continue
        fpath = files[0]
        size = os.path.getsize(fpath)
        mtime = os.path.getmtime(fpath)
        age_hours = (now - mtime) / 3600
        info = {"file": os.path.basename(fpath), "size_bytes": size,
                "size_kb": round(size / 1024, 1), "age_hours": round(age_hours, 1),
                "mtime": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")}
        result["tables"][tab] = info
        if size < 100:
            result["stale"].append(f"{tab}(文件过小:{size}B)")
        elif age_hours > 48:
            result["stale"].append(f"{tab}(超过48小时:{age_hours:.0f}h)")
        else:
            result["ok"].append(tab)

    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True, help="采集产出物根目录（含日期子目录）")
    ap.add_argument("--date", help="检查指定日期（YYYY-MM-DD），缺省检查最近日期")
    ap.add_argument("--json", action="store_true", help="输出JSON")
    args = ap.parse_args()

    if not os.path.isdir(args.raw):
        print(f"错误：采集产出物目录不存在: {args.raw}", file=sys.stderr)
        sys.exit(2)

    date_str = args.date
    if not date_str:
        latest = find_latest_date(args.raw)
        if not latest:
            print("错误：未找到任何日期目录", file=sys.stderr)
            sys.exit(2)
        date_str = os.path.basename(latest)

    result = check_date(args.raw, date_str)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"===== 文件核对（{date_str}）=====")
        print(f"目录: {result['date_dir']}")
        print(f"存在: {'是' if result['exists'] else '否'}")
        print()
        for tab in TABLE_KEYS:
            if tab in result["tables"]:
                info = result["tables"][tab]
                status = "✓" if tab in result["ok"] else "⚠"
                print(f"  {status} {tab:20s} {info['size_kb']:>7}KB  {info['age_hours']:>5}h前  {info['mtime']}")
            else:
                print(f"  ✗ {tab:20s} 缺失")
        print()
        print(f"正常 {len(result['ok'])}/8，缺失 {len(result['missing'])}，异常 {len(result['stale'])}")
        if result["missing"]:
            print(f"缺失表: {', '.join(result['missing'])}")
        if result["stale"]:
            print(f"异常表: {', '.join(result['stale'])}")

    if result["missing"] or result["stale"]:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
