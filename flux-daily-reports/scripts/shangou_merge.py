# -*- coding: utf-8 -*-
"""闪购原始导出文件 → 总表（**保真版**）。

与豆包技能包 `append_master.py` 的差异 —— 只改必要之处：

| # | 差异 | 为什么 |
|---|---|---|
| 1 | **全列按文本处理**，长数字 ID 绝不进 float | 老管线的产物里 19 位订单号被抹掉末几位（平台 `502295362009257681` → `…728`）|
| 2 | 行级去重**不做数值归一** | 数值归一正是把两个末位不同的订单号误判成同一行的原因 |
| 3 | 批次值由**外部传入**（目录名），不从数据行推断 | 老管线那三批的批次值落进了错列 |

**保持不变**：列名并集合并（保持项目既有并集列布局，供已过审的清洗规则使用）、
同批次整体替换、行级去重、按时间列排序。

用法：
    python scripts/shangou_merge.py --raw <原始目录> --master <总表目录> [日期...]
"""
import argparse
import csv
import os
import re
import sys

BATCH_COL = "_采集日期"

# 本次重建**批准的业务日范围**（任务书 MDP-TASK-202 §五之二：2026-08-01 ~ 2026-09-25）。
# 🔴 必须写进代码而不是只写在交付记录里：原始库（`_claude工作区/shangou_raw/`）
#    同时存着 1—7 月的归档文件共 848 份，且它们**都能通过日期校验**，
#    一旦按「全部目录」重建就会把范围外的月份静默并入（第 1 轮审核阻塞项 B2）。
APPROVED_FROM = "2026-08-01"
APPROVED_TO = "2026-09-25"

# 各表用于排序的时间列（与技能包 append_master.py 保持一致）
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

TABLE_KEYS = list(TIME_COLS)

_EMPTY_SORT = "99999999999999"


# ---------------- 纯函数 ----------------
def clean_cell(v):
    """去首尾空白与制表符。**不做任何数值化**（空值符号 `-`/`--` 原样保留）。"""
    return "" if v is None else str(v).strip()


def trim_trailing_empty(header, rows):
    """裁掉表头末尾全空的列（平台导出常在行尾多一个制表符）。"""
    last = len(header)
    while last > 0 and not str(header[last - 1]).strip():
        last -= 1
    new_header = [clean_cell(c) for c in header[:last]]
    new_rows = []
    for row in rows:
        cur = [clean_cell(c) for c in row[:last]]
        if len(cur) < last:
            cur += [""] * (last - len(cur))
        new_rows.append(cur)
    return new_header, new_rows


def union_columns(old, new):
    """列名并集：旧列顺序在前，新列按出现顺序追加。"""
    seen = set(old)
    out = list(old)
    for c in new:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def align_row(row, src_header, full_header):
    """按**列名**把一行对齐到 full_header；缺列填空串，多出的忽略。"""
    idx = {c: i for i, c in enumerate(src_header)}
    out = []
    for c in full_header:
        i = idx.get(c)
        out.append(row[i] if (i is not None and i < len(row)) else "")
    return out


def date_sort_key(value):
    """排序键：`YYYYMMDDHHMMSS`。识别不出的一律排最后。"""
    s = str(value or "")
    m = re.search(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})", s)
    if m:
        key = "%04d%02d%02d" % (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    else:
        m2 = re.search(r"\d{8}", s)
        if not m2:
            return _EMPTY_SORT
        key = m2.group(0)
    tm = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", s)
    if tm:
        key += "%02d%02d%02d" % (int(tm.group(1)), int(tm.group(2)), int(tm.group(3) or 0))
    return key.ljust(14, "0")


def row_days(value):
    """取一个日期单元格里出现的所有日期（YYYY-MM-DD），去重排序。

    `20260915-20260915` → {'2026-09-15'}（同日起止算单日）
    `20260801-20260831` → {'2026-08-01','2026-08-31'}（真区间）
    """
    out = set()
    for y, mo, d in re.findall(r"(\d{4})[-/]?(\d{2})[-/]?(\d{2})", str(value or "")):
        y, mo, d = int(y), int(mo), int(d)
        if 2000 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31:
            out.add("%04d-%02d-%02d" % (y, mo, d))
    return out


def validate_batch(header, rows, table, batch):
    """校验这批行的日期确实都属于 batch 这一天。

    返回 (是否通过, 不合规行数, 首个样例说明)。
    日期列缺失时**放行**（无法校验即不阻断，但调用方应知情）。
    """
    col = TIME_COLS.get(table)
    if not col or col not in header:
        return True, 0, ""
    i = header.index(col)
    bad = 0
    sample = ""
    for r in rows:
        days = row_days(r[i] if i < len(r) else "")
        if days != {batch}:
            bad += 1
            if not sample:
                sample = "%s → %s" % (str(r[i])[:30], sorted(days) or "无日期")
    return bad == 0, bad, sample


def dedup_rows(rows, exclude_idx):
    """行级去重：忽略 exclude_idx 列，**逐字比较**，保留最后出现的那条。

    返回 (kept, dropped)。
    """
    seen = {}
    order = []
    for r in rows:
        key = tuple(str(v) for i, v in enumerate(r) if i != exclude_idx)
        if key in seen:
            seen[key] = r
        else:
            seen[key] = r
            order.append(key)
    kept = [seen[k] for k in order]
    dropped = [r for r in rows if r is not seen.get(
        tuple(str(v) for i, v in enumerate(r) if i != exclude_idx))]
    return kept, dropped


def merge_table(existing, new_header, new_rows, batch):
    """把一批新行并入总表。

    existing: {"header": [...], "rows": [[...]]} 或 None；表头含批次列，行已带批次值。
    返回 {"header": [...], "rows": [...], "stats": {...}}。
    """
    old_header = list(existing["header"]) if existing else []
    old_rows = [list(r) for r in existing["rows"]] if existing else []

    biz_old = [c for c in old_header if c != BATCH_COL]
    biz_new = list(new_header)
    full_header = union_columns(biz_old, biz_new) + [BATCH_COL]

    # ① 删同批次旧行（整体替换）
    dropped_batch = 0
    kept = []
    for r in old_rows:
        b = r[-1] if r else ""
        if b == batch:
            dropped_batch += 1
        else:
            kept.append(r)

    # ② 旧行按列名对齐到并集表头
    if old_rows:
        kept = [align_row(r[:-1], biz_old, full_header[:-1]) + [r[-1] if r else ""]
                for r in kept]

    # ③ 追加新行
    added = [align_row(r, biz_new, full_header[:-1]) + [batch] for r in new_rows]

    # ④ 行级去重
    merged, dropped_dup = dedup_rows(kept + added, exclude_idx=len(full_header) - 1)

    # ⑤ 按时间列排序（保持既有布局）
    tcol = TIME_COLS.get(_table_of_header(full_header))
    if tcol and tcol in full_header:
        ci = full_header.index(tcol)
        merged.sort(key=lambda r: date_sort_key(r[ci] if ci < len(r) else ""))

    return {"header": full_header, "rows": merged,
            "stats": {"replaced_same_batch": dropped_batch,
                      "added": len(added), "deduped": len(dropped_dup)}}


def _table_of_header(header):
    """从表头反推表名（用于取排序时间列）。"""
    for t, col in TIME_COLS.items():
        if col in header:
            return t
    return None


# ---------------- I/O ----------------
def read_csv_text(path):
    """读 CSV，按文本返回 (header, rows)。自动判别 GBK / UTF-8。"""
    with open(path, "rb") as f:
        raw = f.read()
    text = None
    try:
        g = raw.decode("gbk")
        text = raw.decode("utf-8-sig", errors="replace") if "锛" in g else g
    except UnicodeDecodeError:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("gbk", errors="replace")
    rows = list(csv.reader(text.splitlines()))
    rows = [r for r in rows if any(clean_cell(c) for c in r)]
    if not rows:
        return [], []
    return trim_trailing_empty(rows[0], rows[1:])


def write_csv_text(path, header, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fo:
        w = csv.writer(fo)
        w.writerow(header)
        w.writerows(rows)


def read_table(path):
    if not os.path.exists(path):
        return None
    header, rows = read_csv_text(path)
    return {"header": header, "rows": rows}


def table_of(fname):
    base = os.path.basename(fname)
    for k in TABLE_KEYS:
        if base.startswith(k):
            return k
    return None


def split_by_range(days, d_from, d_to):
    """把日期目录名分成本范围内的 / 范围外的，各保持输入顺序。"""
    inside = [d for d in days if d_from <= d <= d_to]
    outside = [d for d in days if not (d_from <= d <= d_to)]
    return inside, outside


def merge_dir(raw_dir, master_dir, dates=None, d_from=None, d_to=None):
    """把 raw_dir/<日期>/ 下的原始文件并入 master_dir/by_table/<表>.csv。

    `dates` 给出则只并这些批次；否则并入 **d_from ~ d_to**（缺省即批准范围
    `APPROVED_FROM` ~ `APPROVED_TO`）。范围外的日期目录**记入日志但不并入** ——
    原始库里另有 1—7 月归档文件，不限定会把它们一起吞进来（审核阻塞项 B2）。
    """
    by_table = os.path.join(master_dir, "by_table")
    os.makedirs(by_table, exist_ok=True)
    log = []
    if dates:
        date_dirs = [os.path.join(raw_dir, d) for d in dates]
    else:
        lo = d_from or APPROVED_FROM
        hi = d_to or APPROVED_TO
        all_days = sorted(
            d for d in os.listdir(raw_dir)
            if os.path.isdir(os.path.join(raw_dir, d)) and re.match(r"\d{4}-\d{2}-\d{2}$", d))
        keep, drop = split_by_range(all_days, lo, hi)
        date_dirs = [os.path.join(raw_dir, d) for d in keep]
        if drop:
            log.append({"table": None, "file": "范围外日期目录",
                        "note": "范围外，跳过 %d 个日期目录（%s ~ %s）；本次只并 %s ~ %s"
                                % (len(drop), drop[0], drop[-1], lo, hi)})
    for ddir in date_dirs:
        batch = os.path.basename(ddir)
        for f in sorted(os.listdir(ddir)):
            if not f.lower().endswith(".csv"):
                continue
            tab = table_of(f)
            if not tab:
                log.append({"table": None, "file": f, "note": "非 8 表之一，跳过"})
                continue
            new_header, new_rows = read_csv_text(os.path.join(ddir, f))
            if not new_rows:
                # 🔴 空文件是**异常**（下载失败留下的空壳），不是「这天没数据」：
                #    事件型表当天无事件时平台根本不产生文件，走的是无数据台账。
                #    以前这里只写一条日志就静默跳过，导致空壳文件把缺口掩盖掉
                #    （第 1 轮审核阻塞项 B1）。现在按拒收处理 —— 会非零退出。
                log.append({"table": tab, "batch": batch, "file": f, "rejected": True,
                            "note": "空文件（0 数据行）—— 不得计为有效文件"})
                continue
            ok, bad, sample = validate_batch(new_header, new_rows, tab, batch)
            if not ok:
                log.append({"table": tab, "batch": batch, "file": f, "rejected": True,
                            "note": "日期校验不过：%d 行不属于 %s（例：%s）" % (bad, batch, sample)})
                continue
            mpath = os.path.join(by_table, tab + ".csv")
            got = merge_table(read_table(mpath), new_header, new_rows, batch)
            write_csv_text(mpath, got["header"], got["rows"])
            log.append({"table": tab, "batch": batch, "file": f,
                        "added": got["stats"]["added"], "total": len(got["rows"]),
                        **got["stats"]})
    return log


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True, help="原始文件根目录（含 YYYY-MM-DD 子目录）")
    ap.add_argument("--master", required=True, help="总表目录（产出 by_table/）")
    ap.add_argument("dates", nargs="*", help="只并入这些批次；缺省用 --from/--to 范围")
    ap.add_argument("--from", dest="d_from", default=APPROVED_FROM,
                    help="起始业务日（缺省 %s）" % APPROVED_FROM)
    ap.add_argument("--to", dest="d_to", default=APPROVED_TO,
                    help="结束业务日（缺省 %s）" % APPROVED_TO)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    log = merge_dir(args.raw, args.master, args.dates or None, args.d_from, args.d_to)
    rejected = [e for e in log if e.get("rejected")]
    if args.json:
        import json
        print(json.dumps(log, ensure_ascii=False, indent=2))
    else:
        print("范围 %s ~ %s（原始库 %s）" % (args.d_from, args.d_to, args.raw))
        for e in log:
            if e.get("table") is None:
                print(f"  ○ 跳过 {e['file']}（{e['note']}）")
                continue
            if e.get("rejected"):
                print(f"  ✗ 拒收 {e['table']}({e['batch']}): {e['note']}")
                continue
            if e.get("note"):
                print(f"  ○ {e['table']}({e['batch']}): {e['note']}")
                continue
            print(f"  ✓ {e['table']}({e['batch']}): +{e['added']}行 → 共{e['total']}行"
                  f" [替换同批次{e['replaced_same_batch']} 去重{e['deduped']}]")
        if rejected:
            print("\n⚠️ 有 %d 个文件被拒收（未并入）：空文件或日期校验不过" % len(rejected))
    return 1 if rejected else 0


if __name__ == "__main__":
    sys.exit(main())
