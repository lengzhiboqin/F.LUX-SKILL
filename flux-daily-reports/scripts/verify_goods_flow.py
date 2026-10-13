#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
商品流量_近30天 内容级比对门（采集后、入库前的独立验收）。

用途：拦截「采集时机早于平台数据就绪点 → 平台静默回退窗口 → 历史稀疏快照」这类错误。
只读，不写库、不联网。判据来自 references/specs/商品流量_近30天.spec.json。

用法：
  python verify_goods_flow.py \
      --file <采集件.xlsx> \
      --expected-end 20261011 \
      --task-name "导出商品流量分析20261012_20260911-20261010" \
      [--prev-file <上一快照.xlsx>] \
      [--min-exposure 100000] [--drop-threshold 0.5]

退出码：0=PASS，2=FAIL（任一硬门不通过），3=文件/结构错误。
"""
import argparse, json, re, sys

HEADER_ROWS = 3
TOTAL_COLS = 30
COL_SPU = 1
COL_NAME = 0
COL_EXPOSURE = 12   # 商品曝光人数 本期
COL_CLICK = 15      # 商品点击人数 本期
COL_ORDER = 18      # 下单人数 本期


def to_num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("%", "")
    if s in ("", "-", "—"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def load(path):
    try:
        import openpyxl
    except ImportError:
        print("缺少 openpyxl", file=sys.stderr); sys.exit(3)
    try:
        # 平台导出件的 sheet 维度元数据可能缺失，read_only 模式会漏列；用全量解析
        wb = openpyxl.load_workbook(path, read_only=False, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
    except Exception as e:
        print(f"无法打开文件 {path}: {e}", file=sys.stderr); sys.exit(3)
    return rows


def parse_window(task_name):
    """从平台任务名提取窗口起止：导出商品流量分析20261012_20260911-20261010 → (start,end)。"""
    if not task_name:
        return None
    m = re.search(r"(\d{8})-(\d{8})", task_name)
    if m:
        return m.group(1), m.group(2)
    m = re.search(r"_(\d{8})(?!\d)", task_name)  # 单日表
    return (m.group(1), m.group(1)) if m else None


def fingerprint(rows):
    ncol = max(len(r) for r in rows) if rows else 0
    body = rows[HEADER_ROWS:]
    summary = None
    goods = []
    for r in body:
        if not r or all(x in (None, "") for x in r):
            continue
        if str(r[COL_NAME]).strip() == "汇总":
            summary = r
        else:
            goods.append(r)
    fp = {
        "total_cols": ncol,
        "goods_rows": len(goods),
        "exposure": to_num(summary[COL_EXPOSURE]) if summary else None,
        "click": to_num(summary[COL_CLICK]) if summary else None,
        "order": to_num(summary[COL_ORDER]) if summary else None,
        "has_summary": summary is not None,
    }
    return fp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--expected-end", required=True, help="期望窗口结束日 YYYYMMDD（应为昨天）")
    ap.add_argument("--task-name", default="", help="平台返回任务名（用于窗口一致性）")
    ap.add_argument("--prev-file", default="", help="上一快照（用于环比连续性）")
    ap.add_argument("--min-exposure", type=float, default=100000,
                    help="品牌全门店近30天汇总曝光下限；全量约百万级，稀疏快照仅数万，默认10万")
    ap.add_argument("--drop-threshold", type=float, default=0.5,
                    help="较上一快照曝光环比下跌超过该比例即判残缺，默认0.5（腰斩）")
    args = ap.parse_args()

    gates = []
    def gate(name, ok, detail, hard=True):
        gates.append({"gate": name, "pass": bool(ok), "hard": hard, "detail": detail})

    rows = load(args.file)
    fp = fingerprint(rows)

    # gate 1：结构
    gate("结构_30列", fp["total_cols"] == TOTAL_COLS,
         f"实际列数 {fp['total_cols']}，期望 {TOTAL_COLS}")
    gate("结构_汇总行", fp["has_summary"], "未找到第一列为『汇总』的汇总行")
    gate("结构_有商品行", fp["goods_rows"] > 0, f"商品明细行数 {fp['goods_rows']}")
    gate("结构_指标非空", fp["exposure"] is not None and fp["click"] is not None,
         f"汇总曝光={fp['exposure']} 点击={fp['click']}")

    # gate 2：窗口一致性（平台任务名结束日必须等于请求结束日）
    win = parse_window(args.task_name)
    if win:
        gate("窗口_结束日一致", win[1] == args.expected_end,
             f"任务名窗口 {win[0]}~{win[1]}，期望结束日 {args.expected_end}")
    else:
        gate("窗口_结束日一致", False, f"无法从任务名解析窗口：{args.task_name!r}（manifest 必须记录任务名）")

    # gate 3：全量量级（品牌全门店近30天曝光下限）
    gate("全量_曝光量级", fp["exposure"] is not None and fp["exposure"] >= args.min_exposure,
         f"汇总曝光 {fp['exposure']}，低于全量下限 {args.min_exposure:g}（疑似历史稀疏快照）")

    # gate 4：环比连续性（与上一快照比，断崖式下跌即残缺）
    if args.prev_file:
        prev = fingerprint(load(args.prev_file))
        if prev["exposure"] and fp["exposure"] is not None:
            ratio = (fp["exposure"] - prev["exposure"]) / prev["exposure"]
            gate("环比_曝光未断崖", ratio >= -args.drop_threshold,
                 f"本期曝光 {fp['exposure']:.0f} vs 上期 {prev['exposure']:.0f}，环比 {ratio:.1%}，"
                 f"下跌超过 {args.drop_threshold:.0%} 判残缺")
        else:
            gate("环比_曝光未断崖", False, "上一快照或本期曝光为空，无法环比")

    hard_fail = [g for g in gates if g["hard"] and not g["pass"]]
    verdict = "PASS" if not hard_fail else "FAIL"
    out = {
        "table": "商品流量_近30天",
        "file": args.file,
        "expected_end": args.expected_end,
        "fingerprint": fp,
        "verdict": verdict,
        "gates": gates,
        "action": "允许入库" if verdict == "PASS" else "拒绝入库：判定数据未就绪/残缺，记录后延后至14:00后重采",
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    sys.exit(0 if verdict == "PASS" else 2)


if __name__ == "__main__":
    main()
