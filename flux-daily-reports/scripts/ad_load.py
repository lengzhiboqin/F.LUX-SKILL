# -*- coding: utf-8 -*-
"""门店推广原始 JSON → 清洗入库（复用 db.py 单事务）。

## 两张事实表

1. `f_门店推广日效果`（核心，按天累积）
   - 粒度：门店 × 业务日期 × 推广产品
   - 来源：history?days=30 的 details（近30天，截至昨日）
   - 用途：投放趋势、花费、效果、同行对标
2. `f_门店推广今日实时`（门店 × 小时，只留当日，整表替换）
   - 来源：today/info 的 today[] 按小时
   - 用途：盘中实时监控

## 口径

- 历史花费 debit：分 → 元；单次点击成本 cpc：分 → 元
- 今日 cost：元（不再换算）
- 曝光/点击/加购/收藏/订单/点击率：各字段 poi
- 同行：各字段 peer

用法：
    python scripts/ad_load.py --raw _data/ad_raw/2026-10-13 --run-day 2026-10-13 \
        --master _data/store_master.json --db _data/test.db
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as DB  # noqa: E402

PROD_LABEL = "一站式推广"
SCOPE = "all"

DAY_COLS = [
    "业务日期", "门店标准ID", "wmPoiId", "门店名称", "推广产品", "推广状态",
    "花费", "曝光", "点击", "点击率", "单次点击成本", "加购", "收藏",
    "订单", "成交金额", "推广时长", "同行曝光", "同行点击", "同行点击率",
    "同行花费", "同行单次点击成本",
]
DAY_KEY = ["业务日期", "门店标准ID", "推广产品"]

TODAY_COLS = [
    "业务日期", "门店标准ID", "wmPoiId", "门店名称", "小时",
    "花费", "曝光", "点击", "点击率", "加购", "收藏", "订单",
]
TODAY_KEY = ["业务日期", "门店标准ID", "小时"]


def fen_to_yuan(v):
    try:
        return round(float(v) / 100.0, 2)
    except (TypeError, ValueError):
        return ""


def poi(x):
    return x.get("poi") if isinstance(x, dict) else ""


def peer(x):
    return x.get("peer") if isinstance(x, dict) else ""


def to_dashed(yyyymmdd):
    s = str(yyyymmdd)
    return "%s-%s-%s" % (s[:4], s[4:6], s[6:8])


def build_day_rows(raw_map, meta):
    rows = []
    for wm, raw in raw_map.items():
        m = meta[wm]
        d30 = json.loads(raw["d30"]["text"])["data"]
        for x in d30.get("details", []):
            cost = fen_to_yuan(poi(x.get("debit")))
            rows.append([
                to_dashed(x["time"]), m["master_id"], wm, m["name"], PROD_LABEL,
                "投放中" if cost not in ("", 0) else "未投放",
                cost, poi(x.get("showCount")), poi(x.get("clickCount")),
                poi(x.get("clickRate")), fen_to_yuan(poi(x.get("cpc"))),
                poi(x.get("addCount")), poi(x.get("collectCount")),
                poi(x.get("orderNum")), poi(x.get("effectOrdAmt")),
                poi(x.get("promotionTime")), peer(x.get("showCount")),
                peer(x.get("clickCount")), peer(x.get("clickRate")),
                fen_to_yuan(peer(x.get("debit"))), fen_to_yuan(peer(x.get("cpc"))),
            ])
    rows.sort(key=lambda r: (r[0], r[1]))
    return rows


def build_today_rows(run_day, raw_map, meta):
    rows = []
    for wm, raw in raw_map.items():
        m = meta[wm]
        td = json.loads(raw["today"]["text"])["data"]
        for h, x in enumerate(td.get("today", [])):
            rows.append([
                run_day, m["master_id"], wm, m["name"], h,
                x.get("cost"), x.get("showCount"), x.get("clickCount"),
                x.get("clickRate"), x.get("addCount"), x.get("collectCount"),
                x.get("orderCount"),
            ])
    return rows


def make_group(table, cols, rows, key, snapshot, source_files):
    return {
        "table": table, "target_table": table, "business_date": "",
        "window_start": None, "window_end": None, "scope_id": SCOPE,
        "status": "validated", "columns": cols, "rows": rows,
        "source_files": source_files, "business_key": key,
        "snapshot": snapshot, "load_to_db": True, "optional_keys": [],
    }


def sha256_file(p):
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--run-day", required=True)
    ap.add_argument("--db", required=True)
    args = ap.parse_args()

    cl = json.load(open(os.path.join(args.raw, "采集清单.json"), encoding="utf-8"))
    raw_map, meta, source_files = {}, {}, []
    for it in cl["items"]:
        wm = it["wmPoiId"]
        if not it.get("file"):
            continue
        p = it["file"]
        raw_map[wm] = json.load(open(p, encoding="utf-8"))
        meta[wm] = {"master_id": it["master_id"], "name": it["name"]}
        source_files.append({"path": p, "sha256": sha256_file(p),
                             "bytes": os.path.getsize(p)})

    day_rows = build_day_rows(raw_map, meta)
    today_rows = build_today_rows(args.run_day, raw_map, meta)

    g_day = make_group("f_门店推广日效果", DAY_COLS, day_rows, DAY_KEY, False, source_files)
    g_today = make_group("f_门店推广今日实时", TODAY_COLS, today_rows, TODAY_KEY, True, source_files)

    batch_id = "AD%s-%s" % (args.run_day.replace("-", ""),
                            time.strftime("%H%M%S"))
    prepared = {
        "batch_id": batch_id, "business_today": args.run_day,
        "rule_version": "v0.3", "groups": [g_day, g_today],
    }
    res = DB.apply_batch(args.db, prepared, args.run_day)
    print("入库完成：日效果 %d 行，今日实时 %d 行" % (len(day_rows), len(today_rows)))
    print("批次:", batch_id, "inserted:", res["inserted"])


if __name__ == "__main__":
    sys.exit(main())
