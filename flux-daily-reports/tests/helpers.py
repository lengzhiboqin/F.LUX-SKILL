# -*- coding: utf-8 -*-
"""T4/T5 测试用的合成夹具：注册表、清洗规则、原件、清单。

**不用真实业务数据**；真实表结构与规则由 `test_rules_integrity` 单独核对。
"""
import csv
import json
import os

from common import contracts as C
from common import registry as REG

T = "2026-10-01"

REGISTRY = {
    "contract_version": "v1",
    "snapshot_window": {"days": 30, "offset": 1},
    "tables": [
        {"table": "门店经营指标", "platform": "牵牛花", "type": "daily",
         "default_offset_days": 1, "expected_cols": 3, "header_rows": 1},
        {"table": "评价数据", "platform": "闪购", "type": "event",
         "default_offset_days": 2, "expected_cols": 3, "header_rows": 1},
    ],
}
HIST = {
    "门店经营指标": {"header_rows": [["序号", "门店名称", "有效订单金额"]],
                 "width": 3, "sheets": ["sheet1"]},
    "评价数据": {"header_rows": [["评价提交日期", "店铺ID", "订单商品"]],
              "width": 3, "sheets": ["csv"]},
}
RULES = {
    "rule_version": "synth-v1",
    "tables": {
        "门店经营指标": {
            "platform": "牵牛花", "type": "daily", "header_rows": 1, "expected_cols": 3,
            "load_to_db": True, "snapshot": False,
            "business_key": ["业务日期", "门店标准ID"],
            "date_source": "filename", "date_source_candidates": [],
            "channel_scope": "全渠道", "drop_cols": ["序号"],
            "renames": {"门店名称": "门店"},
            "money_cols": ["有效订单金额"], "percent_numeric_cols": [],
            "summary_rule": None,
        },
        "评价数据": {
            "platform": "闪购", "type": "event", "header_rows": 1, "expected_cols": 3,
            "load_to_db": True, "snapshot": False,
            "business_key": ["业务日期", "店铺ID", "订单商品"],
            "date_source": "column:评价提交日期", "date_source_candidates": ["评价提交日期"],
            "channel_scope": "美团闪购单渠道", "drop_cols": [],
            "renames": {"评价提交日期": "业务日期"},
            "money_cols": [], "percent_numeric_cols": [],
            "summary_rule": None,
        },
    },
}


def registry():
    r = json.loads(json.dumps(REGISTRY))
    r["_index"] = {t["table"]: t for t in r["tables"]}
    return r


def rules():
    return json.loads(json.dumps(RULES))


def config(tmp, batch_id="B-test"):
    return {"data_root": os.path.join(tmp, "data"), "batch_id": batch_id,
            "_registry": registry()}


def write_csv(path, header, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return path


def qianniu_file(tmp, rows, date="20260930", name="门店经营指标_%s.csv"):
    return write_csv(os.path.join(tmp, "raw", name % date),
                     ["序号", "门店名称", "有效订单金额"], rows)


def shangou_file(tmp, rows, date="20260929"):
    return write_csv(os.path.join(tmp, "raw", "评价数据_%s.csv" % date),
                     ["评价提交日期", "店铺ID", "订单商品"], rows)


def make_task(table, date, scope_id="all"):
    e = registry()["_index"][table]
    return C.make_task(e["platform"], table, date, scope={"scope_id": scope_id})


def collect(tasks, cfg, files, *, statuses=None, evidence=None, hist=None):
    """用假采集器跑一次采集，返回 manifest。`files` 为 `{table: [path,...]}`。"""
    plan = C.make_plan(cfg.get("business_today", T), "targeted", tasks, [])
    statuses = statuses or {}
    evidence = evidence or {}

    def collector(task, _cfg):
        st = statuses.get(task["table"], C.S_DOWNLOADED)
        paths = files.get(task["table"], [])
        fl = []
        for p in paths:
            import hashlib
            with open(p, "rb") as _f:
                h = hashlib.sha256(_f.read()).hexdigest()
            fl.append({"path": p, "bytes": os.path.getsize(p), "sha256": h})
        return {"task_key": task["task_key"], "status": st, "files": fl,
                "evidence": evidence.get(task["table"], {}),
                "error": None}

    return run(cfg, plan, collector, hist)


def run(cfg, plan, collector, hist=None):
    from run_daily import run_collection
    return run_collection(plan, cfg, collector=collector,
                          hist=hist if hist is not None else HIST)


def group(table, date, *, scope_id="all", columns, rows, business_key,
          snapshot=False, status=None, window_start=None, window_end=None,
          platform="牵牛花", sha="0" * 64, load_to_db=True, task_key=None):
    """手工构造一个 PreparedBatch 的 group（用于绕过采集直接测入库）。"""
    return {
        "task_key": task_key or "%s|%s|%s|%s" % (platform, table, date, scope_id),
        "platform": platform, "table": table, "target_table": table,
        "business_date": date, "window_start": window_start, "window_end": window_end,
        "scope_id": scope_id, "status": status or C.S_VALIDATED,
        "columns": list(columns), "rows": [list(r) for r in rows],
        "source_files": [{"path": "synthetic://%s_%s" % (table, date),
                          "sha256": sha, "bytes": 1}],
        "source_rows": len(rows), "dedup_dropped": 0, "cleaning_log": [],
        "business_key": list(business_key), "snapshot": snapshot, "load_to_db": load_to_db,
    }


def prepared(batch_id, groups, *, business_today=T, rule_version="synth-v1"):
    return {"batch_id": batch_id, "business_today": business_today,
            "rule_version": rule_version, "source_files": [],
            "groups": groups,
            "stats": {"groups": len(groups)}}
