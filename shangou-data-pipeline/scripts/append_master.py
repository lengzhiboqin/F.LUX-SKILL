# -*- coding: utf-8 -*-
"""闪购8张表数据追加到总表：master/by_table/<表名>.csv 按表累积。

去重两层：
1) 分区替换：总表带 _采集日期 列，同表同采集日重跑时该分区整体替换；
2) 行级去重：完全相同的业务行（忽略 _采集日期 列）只保留最新一次采集的。

旧格式迁移：如果总表没有 _采集日期 列，自动添加该列（值从文件名日期推断或标记为"unknown"）。

用法：
  python3 append_master.py --raw <raw_dir> --master <master_dir> [日期...]
  日期缺省时扫描 raw/ 下所有日期目录全部并入。
"""
import argparse, csv, glob, json, os, re, sys
from datetime import datetime

TABLE_KEYS = ["门店财务明细", "门店成交明细", "商品数据", "问题订单数据",
              "流量明细(新)", "流量渠道明细(新)", "评价数据", "售后订单数据"]
TAG = "_采集日期"

# 各表用于排序的时间列
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
    """排序键：兼容多种日期时间格式。"""
    s = str(v)
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


def read_csv_auto(path):
    """读CSV，自动判别 GBK/UTF-8 编码。"""
    raw = open(path, "rb").read()
    try:
        g = raw.decode("gbk")
        if "锛" in g:
            text = raw.decode("utf-8-sig", errors="replace")
        else:
            text = g
    except UnicodeDecodeError:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("gbk", errors="replace")
    rows = list(csv.reader(text.splitlines()))
    return [r for r in rows if any(c.strip() for c in r)]


def table_of(fname):
    base = os.path.basename(fname)
    for k in TABLE_KEYS:
        if base.startswith(k):
            return k
    return None


def date_from_filename(fname):
    """从文件名提取日期，如 门店财务明细_2026-09-14.csv -> 2026-09-14。"""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(fname))
    return m.group(1) if m else "unknown"


def normalize_header(header, rows):
    """裁掉表头和数据行末尾完全为空的列。"""
    if not header:
        return header, rows
    last = len(header)
    while last > 0 and not str(header[last - 1]).strip():
        last -= 1
    nh = list(header[:last])
    nr = []
    for row in rows:
        cur = list(row[:last])
        if len(cur) < last:
            cur += [""] * (last - len(cur))
        nr.append(cur)
    return nh, nr


def _dedup_key(row, exclude_idx):
    """行级去重键：忽略 _采集日期 列；数值字符串按浮点值归一化。

    备份数据金额常为 572.12，采集数据为 572.1200，字符串不同但数值相同，
    直接全列比较会误判为不同行导致重复。归一化后两者视为同一行。
    """
    parts = []
    for i, v in enumerate(row):
        if i == exclude_idx:
            continue
        s = str(v).strip()
        if s and re.fullmatch(r"-?\d+(\.\d+)?", s):
            try:
                parts.append(("num", repr(float(s))))
                continue
            except ValueError:
                pass
        parts.append(("str", s))
    return tuple(parts)


def migrate_old_master(header, body, table_name):
    """迁移旧格式总表：如果没有 _采集日期 列，自动添加。
    旧数据的采集日期标记为 'legacy'，后续新数据用真实日期。
    """
    if TAG in header:
        return header, body, False
    new_header = header + [TAG]
    new_body = [r + ["legacy"] for r in body]
    return new_header, new_body, True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True, help="原始报表目录（含日期子目录）")
    ap.add_argument("--master", required=True, help="总表目录（by_table 子目录）")
    ap.add_argument("dates", nargs="*", help="指定要并入的日期，缺省全部")
    ap.add_argument("--json", action="store_true", help="输出JSON结果")
    args = ap.parse_args()

    master_dir = os.path.join(args.master, "by_table")
    os.makedirs(master_dir, exist_ok=True)

    # 确定要处理的日期目录
    if args.dates:
        date_dirs = [os.path.join(args.raw, d) for d in args.dates]
    else:
        date_dirs = sorted(glob.glob(os.path.join(args.raw, "*")))
        date_dirs = [p for p in date_dirs if os.path.isdir(p)
                     and re.match(r"\d{4}-\d{2}-\d{2}$", os.path.basename(p))]

    log = {"merged": {}, "skipped": [], "migrated": [], "rejected": []}

    for ddir in date_dirs:
        tag = os.path.basename(ddir)
        for f in sorted(glob.glob(os.path.join(ddir, "*.csv"))):
            tab = table_of(os.path.basename(f))
            if not tab:
                log["skipped"].append(os.path.basename(f))
                continue
            new_rows = read_csv_auto(f)
            if len(new_rows) < 1:
                continue
            header, body = normalize_header(new_rows[0], new_rows[1:])
            mpath = os.path.join(master_dir, tab + ".csv")

            # 读取/初始化总表
            old_rows = read_csv_auto(mpath) if os.path.exists(mpath) else []
            if old_rows:
                old_header, old_body = normalize_header(old_rows[0], old_rows[1:])
                # 迁移旧格式
                old_header, old_body, migrated = migrate_old_master(old_header, old_body, tab)
                if migrated:
                    log["migrated"].append(tab)
            else:
                old_header = None
                old_body = []

            # 表头处理：自动合并取并集（缺失字段留空），关键列缺失才拒收
            header_warning = None
            if old_header and TAG in old_header:
                old_biz = [c for c in old_header if c != TAG]
                old_biz_set = set(old_biz)
                new_set = set(header)
                if old_biz_set != new_set:
                    merged_biz = list(old_biz)
                    for c in header:
                        if c not in old_biz_set:
                            merged_biz.append(c)
                    missing = sorted(old_biz_set - new_set)
                    extra = sorted(new_set - old_biz_set)
                    header_warning = "表头已合并: 缺列=%s 新增列=%s" % (missing[:5], extra[:5])
                    old_idx = {c: i for i, c in enumerate(old_header)}
                    old_body = [[r[old_idx[c]] if c in old_idx and old_idx[c] < len(r) else ""
                                 for c in merged_biz] + [r[old_idx[TAG]] if TAG in old_idx and old_idx[TAG] < len(r) else ""]
                                for r in old_body]
                    full_header = merged_biz + [TAG]
                else:
                    full_header = old_header
            elif old_header:
                full_header = old_header + [TAG] if TAG not in old_header else old_header
            else:
                full_header = header + [TAG]

            ti = full_header.index(TAG)
            biz_cols = len(full_header) - 1
            new_idx = {c: i for i, c in enumerate(header)}

            def align_row(r):
                return [r[new_idx[c]] if c in new_idx and new_idx[c] < len(r) else ""
                        for c in full_header[:-1]]

            # 1) 去掉同采集日期旧分区
            kept = [r for r in old_body if (r[ti] if ti < len(r) else "") != tag]
            # 2) 追加新行
            kept += [align_row(r) + [tag] for r in body]
            # 3) 行级去重（忽略 _采集日期，保留最新；数值列按浮点归一化）
            seen = {}
            for r in kept:
                key = _dedup_key(r, ti)
                seen[key] = r
            merged = list(seen.values())
            # 4) 按时间列升序
            tcol = TIME_COLS.get(tab)
            if tcol and tcol in full_header:
                ci = full_header.index(tcol)
                merged.sort(key=lambda r: _date_key(r[ci] if ci < len(r) else ""))

            with open(mpath, "w", encoding="utf-8-sig", newline="") as fo:
                w = csv.writer(fo)
                w.writerow(full_header)
                w.writerows(merged)

            # ===== 写入验证（防止假成功） =====
            verify_ok = True
            verify_note = ""
            try:
                verify_rows = read_csv_auto(mpath)
                verify_count = len(verify_rows) - 1  # 减去表头
                if verify_count != len(merged):
                    verify_ok = False
                    verify_note = f"写入验证失败: 预期{len(merged)}行，实际{verify_count}行"
                else:
                    # 验证采集日期是否存在
                    if verify_rows:
                        verify_header = verify_rows[0]
                        if TAG in verify_header:
                            ti_v = verify_header.index(TAG)
                            dates_found = set(r[ti_v] for r in verify_rows[1:] if ti_v < len(r))
                            if tag not in dates_found:
                                verify_ok = False
                                verify_note = f"写入验证失败: 采集日期{tag}未在总表中找到"
            except Exception as e:
                verify_ok = False
                verify_note = f"写入验证异常: {str(e)[:80]}"

            log["merged"][tab] = {
                "src": os.path.basename(f),
                "collect_date": tag,
                "added": len(body),
                "new_total": len(merged),
                "verify_ok": verify_ok,
                "verify_note": verify_note,
                "header_warning": header_warning,
            }
            if not verify_ok:
                log.setdefault("verify_failed", []).append({"table": tab, "date": tag, "note": verify_note})
            if header_warning:
                log.setdefault("header_merged", []).append({"table": tab, "date": tag, "warning": header_warning})

    # 输出结果
    if args.json:
        print(json.dumps(log, ensure_ascii=False, indent=2))
    else:
        print("===== 追加结果 =====")
        for tab, info in log["merged"].items():
            warn = f" [表头合并]" if info.get("header_warning") else ""
            verify = "" if info.get("verify_ok", True) else f" ⚠️ {info.get('verify_note', '验证失败')}"
            print(f"  ✓ {tab}: +{info['added']}行 → 总计{info['new_total']}行 ({info['collect_date']}){warn}{verify}")
        if log["migrated"]:
            print(f"  ⚠ 旧格式迁移: {', '.join(log['migrated'])}")
        if log.get("header_merged"):
            print(f"  ⚠ 表头自动合并: {len(log['header_merged'])} 张表（缺失字段留空）")
        if log.get("verify_failed"):
            print(f"  ✗ 写入验证失败: {len(log['verify_failed'])} 张表")
            for vf in log["verify_failed"]:
                print(f"    - {vf['table']}({vf['date']}): {vf['note']}")
        if log["rejected"]:
            print(f"  ✗ 拒收: {len(log['rejected'])} 个文件")
            for r in log["rejected"]:
                print(f"    - {r['table']}({r['date']}): {r['reason'][:80]}")
        if log["skipped"]:
            print(f"  ○ 跳过(非8表): {len(log['skipped'])} 个文件")
        print(f"\n成功 {len(log['merged'])} 表，验证失败 {len(log.get('verify_failed', []))}，拒收 {len(log['rejected'])}，迁移 {len(log['migrated'])}，表头合并 {len(log.get('header_merged', []))}")

    # 有拒收或验证失败则退出码1
    has_error = log["rejected"] or log.get("verify_failed")
    sys.exit(1 if has_error else 0)


if __name__ == "__main__":
    main()
