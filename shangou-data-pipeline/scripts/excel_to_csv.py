# -*- coding: utf-8 -*-
"""将用户的 Excel 总表（F.LUX品牌数据汇总表.xlsx）迁移为标准 CSV 总表格式。

标准格式：master/by_table/<表名>.csv，UTF-8-BOM，带 _采集日期 列，按时间列升序。
旧数据的 _采集日期 保留原值（如果Excel已有该列），否则标记为 "legacy"。

用法：
  python3 excel_to_csv.py --excel <总表.xlsx> --master <总表目录>
"""
import argparse, csv, json, os, re, sys

try:
    import pandas as pd
except ImportError:
    print("需要 pandas: pip install pandas openpyxl", file=sys.stderr)
    sys.exit(3)

TABLE_KEYS = ["门店财务明细", "门店成交明细", "商品数据", "问题订单数据",
              "流量明细(新)", "流量渠道明细(新)", "评价数据", "售后订单数据"]
TAG = "_采集日期"

TIME_COLS = {
    "门店财务明细": "开始时间",
    "门店成交明细": "开始日期",
    "商品数据": "日期",
    "问题订单数据": "下单时间",
    "流量明细(新)": "日期",
    "流量渠道明细(新)": "日期",
    "评价数据": "评价提交日期",
    "售后订单数据": "下单时间",
}


def _date_key(v):
    s = str(v).strip()
    m = re.search(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})", s)
    if m:
        key = "%04d%02d%02d" % (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    else:
        m2 = re.search(r"\d{8}", s)
        if not m2:
            return "99999999000000"
        key = m2.group(0)
    tm = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", s)
    if tm:
        key += "%02d%02d%02d" % (int(tm.group(1)), int(tm.group(2)), int(tm.group(3) or 0))
    return key


def clean_value(v):
    if pd.isna(v):
        return ""
    s = str(v).strip()
    while s.startswith("\t"):
        s = s[1:]
    return s.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--excel", required=True, help="Excel 总表路径")
    ap.add_argument("--master", required=True, help="总表输出目录（会创建 by_table 子目录）")
    args = ap.parse_args()

    if not os.path.exists(args.excel):
        print(f"错误：Excel 文件不存在: {args.excel}", file=sys.stderr)
        sys.exit(1)

    master_dir = os.path.join(args.master, "by_table")
    os.makedirs(master_dir, exist_ok=True)

    xls = pd.ExcelFile(args.excel)
    print(f"Excel sheet 列表: {xls.sheet_names}")

    migrated = []
    skipped = []

    for sn in xls.sheet_names:
        if sn not in TABLE_KEYS:
            skipped.append(sn)
            continue

        df = pd.read_excel(xls, sheet_name=sn)
        if len(df) == 0:
            print(f"  ○ {sn}: 空表，跳过")
            skipped.append(sn)
            continue

        df = df.map(clean_value)

        if TAG not in df.columns:
            df[TAG] = "legacy"

        tcol = TIME_COLS.get(sn)
        if tcol and tcol in df.columns:
            df = df.sort_values(by=tcol, key=lambda s: s.map(_date_key), kind="stable")

        out_path = os.path.join(master_dir, sn + ".csv")
        df.to_csv(out_path, index=False, encoding="utf-8-sig")

        migrated.append({"table": sn, "rows": len(df), "cols": len(df.columns)})
        print(f"  ✓ {sn}: {len(df)}行 × {len(df.columns)}列 → {out_path}")

    print(f"\n迁移完成：成功 {len(migrated)} 表，跳过 {len(skipped)} 个 sheet（{skipped}）")
    print(f"总表目录: {master_dir}")

    summary = {
        "excel": args.excel,
        "master_dir": master_dir,
        "migrated": migrated,
        "skipped": skipped,
    }
    summary_path = os.path.join(args.master, "migration_summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"迁移摘要: {summary_path}")


if __name__ == "__main__":
    main()
