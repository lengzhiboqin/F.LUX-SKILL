# -*- coding: utf-8 -*-
"""平台内去重：`out/normalize/<表>.csv` → `out/dedup/<表>.csv`（17 张）。

依据 [整合与清洗规则](../10-事实基准/整合与清洗规则.md) **§二 规则 14** / **§4.4.1** / **§4.4.5**
与 [ADR-0011](../10-事实基准/ADR/0011-merge-rules.md) **规则 2**（**均已通过第三方审核**）。

## 为什么「短键 + 批次」而不是「整行当键」

平台会**回溯修订**同一笔业务：实测（规则 §4.4.1）第 2024 / 15896 / 19463 / 22689 行的
下单时间、订单编号、SKU、商品名、数量、实付**全同**，**只有「优惠活动」不同**。
用整行当键会把它们当成 4 个商品 → **销量 1 算成 4、实付 7.85 算成 31.40**。

**但也不能只按短键去重**：同批次内的**独立明细**是真明细
（实测第 24659/24660/24661 行同批次、数量 `1/2/1`）—— 合掉就是**丢行**。

所以规则是 **① 短键分组 → ② 取批次 ③ 只留最新批次的全部行**，两步都不可省。

## 🔴 批次标记读 `采集时间`，不是 `_采集日期`

规则原文写的是 `_采集日期`，但 MDP-TASK-203（规整）按规则把这列**丢弃**了，
**它的值被搬进了 `采集时间`**（实测 56 种取值，与原始 `_采集日期` 逐一对应）。

## 一张表用哪条规则（逐表声明，不猜）

| 表 | 规则 | 依据 |
|---|---|---|
| 商品数据 | `dedup_goods` —— 短键 + 批次 | 规则 §4.4.1（**唯一有跨批次重复的表**）|
| 售后订单数据 | `classify_after_sale` —— **只打标记，不丢行** | 规则 §4.4.5（按行判定，错位段原样保全）|
| 评价数据 | `merge_reviews` —— 逐列非空覆盖 | 实施计划 Task 2.3 |
| 其余 14 张 | **原样透传** | 规则 §4.4 实测「跨批次重复只有商品数据有」|

## 输入输出

    python scripts/dedup.py                       # 全部 17 张
    python scripts/dedup.py --table 商品数据      # 只做一张

- 输入：`out/normalize/<表>.csv`（MDP-TASK-203 的产物，**不入库**）
- 输出：`out/dedup/<表>.csv` + `out/dedup/_丢弃记录.json`（**不入库**）
"""
import argparse
import csv
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from normalize import EXCLUDED_TABLES, TABLES, SUMMARY_COLS  # noqa: E402

IN_DIR = os.path.join(ROOT, "out", "normalize")
OUT_DIR = os.path.join(ROOT, "out", "dedup")

# 🔴 批次标记列 —— 见文件头「批次标记读 `采集时间`」
BATCH_COL = "采集时间"

# `_是否汇总行` 与门店标准ID 是**派生列**，不算业务内容（兜底去重时不参与指纹）
DERIVED_COLS = (BATCH_COL, SUMMARY_COLS, "门店标准ID")

# 售后错位标记列名 —— **规则 §4.4.5 原文指定的名字，不得改写**
AFTER_SALE_FLAG = "_售后列段疑错位"

# 🔴 短键**逐表声明**（规则 §二 规则 14 / §4.4.1 / §4.4.2 / §4.4.4）
#    没有列在这里的表 = **不做短键去重**，原样透传
SHORT_KEYS = {
    # 规则 §4.4.1 第 ③ 步「分组键（短键）」
    "商品数据": ("下单时间", "订单编号", "商品SKU码", "商品名称"),
    # 规则 §4.4.5 主键
    "售后订单数据": ("展示订单号",),
    # 计划 Task 2.3 的评价短键
    "评价数据": ("评价提交时间", "店铺ID", "订单商品"),
}

# 丢弃原因（写进 `_丢弃记录.json`，**不静默**）
R_EARLIER_BATCH = "更早批次（同短键只留最新批次的全部行）"
R_EXACT_DUP = "同批次内整行重复（规则 §4.4.1 第 ⑤ 步兜底）"
R_MERGED = "并入同一条评价的更新批次（逐列非空覆盖）"

# 售后四分支 —— 规则 §4.4.5 的判据表，**逐条对应，不自行发明**
ALIGNED = "aligned"          # 具体日期 → 对齐行
BATCH_MARKER = "batch_marker"  # `2026-08-31-month` → 批次标记行
MISPLACED = "misplaced"      # 其他文本（如「长时间无骑手接单」）→ 确认错位行
UNKNOWN = "unknown"          # 空值 → 无法判定

# 规则 §4.4.5 写的年份是 2026；写成 `\d{4}` 是**同义的一般化**（判据是「是不是一个具体日期」）
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATE_SUFFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\w+$")
# 兼容 `20260831` 紧凑写法
_COMPACT_DATE_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")


def _s(v):
    return "" if v is None else str(v).strip()


def batch_order(label):
    """批次排序键；**同日 `-month` 更晚**（月末汇总在日批次之后）。

    认三种写法：`2026-08-31` · `2026-08-31-month` · `20260831`。

    ⚠️ **认不出来的一律排最前**（`(0, 0, 0, 0)`）—— 调用方必须把这类行记进
    `_丢弃记录.json`（带批次标签），**不得静默**。实测当前数据 56 种取值
    **全是纯日期**，走不到这条分支。
    """
    s = _s(label)
    if _DATE_RE.match(s):
        y, m, d = s.split("-")
        return (int(y), int(m), int(d), 0)
    if _DATE_SUFFIX_RE.match(s):
        y, m, d, _suffix = s.split("-", 3)
        return (int(y), int(m), int(d), 1)
    m = _COMPACT_DATE_RE.match(s)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)), 0)
    return (0, 0, 0, 0)


def short_key(row, table):
    """按**该表声明的短键**取值；没声明的表返回 `None`（=不做短键去重）。"""
    keys = SHORT_KEYS.get(table)
    if not keys:
        return None
    return tuple(_s(row.get(k)) for k in keys)


def _fingerprint(row):
    """业务内容指纹 —— 用于规则 §4.4.1 第 ⑤ 步的兜底去重。

    🔴 **排除批次列与派生列**，否则「同一笔业务的两个批次」指纹必然不同，兜底失效。
    """
    return tuple(_s(v) for c, v in row.items() if c not in DERIVED_COLS)


def _diff_cols(old, new):
    """两行之间**被改过的列**（规则 §4.4.1 第 ④ 步「记差异」）。"""
    out = {}
    for c, v in new.items():
        if c in DERIVED_COLS:
            continue
        if _s(v) and _s(v) != _s(old.get(c)):
            out[c] = {"旧": _s(old.get(c)), "新": _s(v)}
    return out


def _drop_entry(row, reason, table, kept=None):
    entry = {
        "原因": reason,
        "短键": " | ".join(short_key(row, table) or ()),
        "批次": _s(row.get(BATCH_COL)),
        "行": {c: _s(v) for c, v in row.items()},
    }
    if kept is not None:
        entry["保留批次"] = _s(kept.get(BATCH_COL))
        entry["与保留行的差异"] = _diff_cols(kept, row)
    return entry


# ---------------- 商品数据：短键 + 批次 ----------------
def dedup_goods(rows, table="商品数据"):
    """**短键 + 批次**去重 → `(kept, dropped)`。

    规则 §4.4.1 的五步：
    ① 分组键 = 短键；② 批次 = `采集时间`（同日 `-month` 更晚）；
    ③ 同短键**只留最新批次里的全部行**（同批次内的独立明细**全留**）；
    ④ 丢弃行的不同值**记入 dropped，不静默**；
    ⑤ 兜底：保留后若仍有「业务内容完全相同」的行，**再去一次重**。
    """
    newest, latest_row = {}, {}
    for r in rows:
        k = short_key(r, table)
        b = batch_order(r.get(BATCH_COL))
        if k not in newest or b > newest[k]:
            newest[k], latest_row[k] = b, r

    kept, dropped = [], []
    for r in rows:
        k = short_key(r, table)
        if batch_order(r.get(BATCH_COL)) == newest[k]:
            kept.append(r)
        else:
            dropped.append(_drop_entry(r, R_EARLIER_BATCH, table, kept=latest_row[k]))

    # ⑤ 兜底去重（规则实测为 0 行，留作幂等保护）
    seen, final = set(), []
    for r in kept:
        fp = _fingerprint(r)
        if fp in seen:
            dropped.append(_drop_entry(r, R_EXACT_DUP, table))
            continue
        seen.add(fp)
        final.append(r)
    return final, dropped


# ---------------- 评价数据：逐列非空覆盖 ----------------
def merge_reviews(rows, table="评价数据"):
    """评价的多批次合并 → `(merged, dropped)`。

    **逐列非空覆盖**：按批次从旧到新，**空值不冲掉旧值**（后批次的空 = 没填，不是改成空）。
    合并后每个短键**只留一行**。
    """
    groups = {}
    for r in rows:
        groups.setdefault(short_key(r, table), []).append(r)

    merged, dropped = [], []
    for grp in groups.values():
        if len(grp) == 1:
            merged.append(grp[0])
            continue
        ordered = sorted(grp, key=lambda r: batch_order(r.get(BATCH_COL)))
        base = dict(ordered[0])
        for r in ordered[1:]:
            for c, v in r.items():
                if _s(v):          # 非空即覆盖；空**不冲掉**旧值（后批次的空 = 没填）
                    base[c] = v
            dropped.append(_drop_entry(r, R_MERGED, table, kept=base))
        merged.append(base)
    return merged, dropped


# ---------------- 售后订单数据：只打标记，不丢行 ----------------
def classify_after_sale(row):
    """售后错位段的**按行**判定 → 四分支之一（规则 §4.4.5 的判据表）。

    判据列是 `采集时间`（规则原文写 `_采集日期`，因规整已丢弃该列而改用，见文件头）。

    ⚠️ **用户 2026-09-28 裁定：本函数按原计划保留并测试，
    不扩大到回头修旧总表的历史错位。** 重建后实测 0 行错位，
    真实数据上**全部判为 `aligned` 是预期内的正确结果**。
    """
    s = _s(row.get(BATCH_COL))
    if not s:
        return UNKNOWN
    if _DATE_RE.match(s):
        return ALIGNED
    if _DATE_SUFFIX_RE.match(s):
        return BATCH_MARKER
    return MISPLACED


def mark_after_sale(rows):
    """给每行加 `_售后列段疑错位`，返回 `(rows, 各类行数)`。

    **错位行与无法判定行原样保全、只打标记** —— 规则 §4.4.5 明确：
    错位行中另有 19 条「展示订单号」全表只出现一次，**整段丢弃就永久没了**。
    """
    stats = {ALIGNED: 0, BATCH_MARKER: 0, MISPLACED: 0, UNKNOWN: 0}
    for r in rows:
        kind = classify_after_sale(r)
        stats[kind] += 1
        r[AFTER_SALE_FLAG] = "1" if kind in (MISPLACED, UNKNOWN) else "0"
    # 标记列放在最后，不改动上游列序
    return rows, stats


# ---------------- 逐表调度 ----------------
def dedup_table(table, rows):
    """按**该表声明的规则**处理 → `(rows, dropped, 备注)`。"""
    if table in SHORT_KEYS and table != "售后订单数据":
        fn = dedup_goods if table != "评价数据" else merge_reviews
        kept, dropped = fn(rows)
        return kept, dropped, {}
    if table == "售后订单数据":
        rows, stats = mark_after_sale(rows)
        return rows, [], stats
    return rows, [], {}


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        return list(rd.fieldnames), [dict(r) for r in rd]


def write_csv(path, cols, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", help="只做一张表")
    ap.add_argument("--in-dir", default=IN_DIR)
    ap.add_argument("--out", default=OUT_DIR)
    args = ap.parse_args()

    todo = [args.table] if args.table else [t for t in TABLES if t not in EXCLUDED_TABLES]
    os.makedirs(args.out, exist_ok=True)

    ledger, total_in, total_out = {}, 0, 0
    print("%-18s %8s %8s %8s" % ("表", "输入", "输出", "丢弃"))
    for t in todo:
        src = os.path.join(args.in_dir, t + ".csv")
        if not os.path.exists(src):
            print("%-18s   —— 缺输入 %s" % (t, src))
            continue
        cols, rows = read_csv(src)
        out_rows, dropped, extra = dedup_table(t, rows)
        if t == "售后订单数据":
            cols = cols + [AFTER_SALE_FLAG]
        write_csv(os.path.join(args.out, t + ".csv"), cols, out_rows)
        ledger[t] = {
            "输入行数": len(rows), "输出行数": len(out_rows), "丢弃行数": len(dropped),
            "丢弃": dropped,
        }
        if extra:
            ledger[t]["售后判定"] = {
                ALIGNED: extra[ALIGNED], BATCH_MARKER: extra[BATCH_MARKER],
                MISPLACED: extra[MISPLACED], UNKNOWN: extra[UNKNOWN],
            }
        total_in += len(rows)
        total_out += len(out_rows)
        note = ""
        if extra:
            note = "  对齐%d/批次%d/错位%d/无法判定%d" % (
                extra[ALIGNED], extra[BATCH_MARKER], extra[MISPLACED], extra[UNKNOWN])
        print("%-18s %8d %8d %8d%s" % (t, len(rows), len(out_rows), len(dropped), note))

    with open(os.path.join(args.out, "_丢弃记录.json"), "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=1)
    print("\n合计：输入 %d 行 → 输出 %d 行，丢弃 %d 行" % (total_in, total_out, total_in - total_out))
    print("丢弃记录：%s" % os.path.join(args.out, "_丢弃记录.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
