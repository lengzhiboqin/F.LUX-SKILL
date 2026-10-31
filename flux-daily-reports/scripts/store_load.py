# -*- coding: utf-8 -*-
"""门店商品全量 xlsx → 两张表入库（复用 db.py 单事务）。

## 两张表

1. `f_门店商品全量快照`（全量 88 列，每天整表替换，恒定约 134 MB）
   - 粒度：门店 × SPU × SKU
   - 用途：任意时刻查某商品在某店的状态/库存/价格/上下线
2. `f_门店商品缺勤明细`（精简 20 列，只留有动销缺勤，按天累积）
   - 缺勤口径：**上下线状态='已上线' 且 渠道库存=0 且 月销量>0（近30天有动销）**
   - 缺勤类型：实物库存=0 → '真断货'；实物库存>0 → '有货未同步'
   - 实测 1355 行/天，约 407 B/行 → 年约 200 MB（远小于平台口径）

用法：
    python scripts/store_load.py --file _data/store_raw/门店商品全量_20261013.xlsx \
        --run-day 2026-10-13 --db _data/test.db
"""
import argparse
import hashlib
import json
import os
import sys
import time

import openpyxl

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as DB  # noqa: E402

SNAPSHOT_COLS = [
    "SPUID", "SKUID", "门店标准ID", "门店名称", "门店所属省份", "门店所属城市",
    "商品类型", "商品条码", "商品名称", "规格名称", "美团闪购渠道销售属性",
    "饿了么渠道销售属性", "渠道库存", "按售罄同步渠道", "实物库存",
    "总部店内分类", "总部店内分类末级分类", "门店店内分类", "门店店内分类末级分类",
    "售卖状态", "规格上下架状态", "上下线状态", "是否手工下架", "手工下架时间",
    "手工下架操作人", "是否有图片", "是否手工停售", "手工停售时间",
    "手工停售操作人", "是否售罄允许上架", "是否售罄允许上架设置时间",
    "线上重量", "线上重量单位", "美团闪购类目一级", "美团闪购类目二级",
    "美团闪购类目三级", "饿了么类目一级", "饿了么类目二级", "饿了么类目三级",
    "生产日期", "到期日期", "是否临期", "美团闪购渠道售价", "饿了么渠道售价",
    "不符渠道规则类型", "不符渠道规则备注", "采购价", "美团店内码/货号",
    "美团闪购开放平台唯一标识", "美团闪购平台SPUID", "外部编码", "起购数量",
    "商品卖点", "是否力荐", "可售时间", "商品描述", "美团闪购无理由退货",
    "商品属性", "商品类目", "品牌名称", "月销量", "美团闪购质量审核状态",
    "美团闪购合规审核状态", "美团闪购不可售原因", "SPU商品标签", "SKU商品标签",
    "货品单位1", "转换比例1", "货品单位2", "转换比例2", "货品单位3",
    "转换比例3", "货品单位4", "转换比例4", "货品单位5", "转换比例5",
    "货品单位6", "转换比例6", "货品单位7", "转换比例7", "货品单位8",
    "转换比例8", "货品单位9", "转换比例9", "货品单位10", "转换比例10",
    "门店商品自编码", "售卖渠道",
]

ABSENT_COLS = [
    "业务日期", "门店标准ID", "门店名称", "门店所属城市", "SPUID", "SKUID",
    "商品名称", "规格名称", "商品条码", "美团闪购渠道售价", "月销量",
    "渠道库存", "实物库存", "上下线状态", "售卖状态", "缺勤类型", "商品类型",
    "品牌名称", "美团闪购类目一级", "美团闪购类目二级",
]
ABSENT_KEY = ["业务日期", "门店标准ID", "SKUID"]


def is_zero(v):
    return v in (0, "0")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--run-day", required=True)
    ap.add_argument("--db", required=True)
    args = ap.parse_args()

    wb = openpyxl.load_workbook(args.file, read_only=False, data_only=True)
    ws = wb.active
    snap_rows, absent_rows = [], []
    for r in ws.iter_rows(min_row=2, values_only=True):
        # 原始列顺序与 SNAPSHOT_COLS 对齐（r0=SPUID, r1=SKUID, r2=门店编码→门店标准ID）
        base = (list(r) + [""] * len(SNAPSHOT_COLS))[:len(SNAPSHOT_COLS)]
        snap_rows.append(base)

        online = r[21]
        stock = r[12]
        month = str(r[60]).strip() if r[60] is not None else ""
        if online == "已上线" and is_zero(stock) and month:
            entity = r[14]
            atype = "真断货" if is_zero(entity) else "有货未同步"
            absent_rows.append([
                args.run_day, r[2], r[3], r[5], r[0], r[1], r[8], r[9],
                r[7], r[42], r[60], stock, entity, online, r[19], atype,
                r[6], r[59], r[33], r[34],
            ])
    wb.close()

    def sha(p):
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for c in iter(lambda: f.read(1 << 16), b""):
                h.update(c)
        return h.hexdigest()

    sf = [{"path": os.path.abspath(args.file), "sha256": sha(args.file),
           "bytes": os.path.getsize(args.file)}]

    def g(table, cols, rows, key, snapshot):
        return {"table": table, "target_table": table, "business_date": args.run_day,
                "window_start": None, "window_end": None, "scope_id": "all",
                "status": "validated", "columns": cols, "rows": rows,
                "source_files": sf, "business_key": key, "snapshot": snapshot,
                "load_to_db": True, "optional_keys": []}

    prepared = {
        "batch_id": "STORE%s-%s" % (args.run_day.replace("-", ""),
                                   time.strftime("%H%M%S")),
        "business_today": args.run_day, "rule_version": "v0.3",
        "groups": [
            g("f_门店商品全量快照", SNAPSHOT_COLS, snap_rows, [], True),
            g("f_门店商品缺勤明细", ABSENT_COLS, absent_rows, ABSENT_KEY, False),
        ],
    }
    res = DB.apply_batch(args.db, prepared, args.run_day)
    print("全量快照 %d 行，缺勤明细 %d 行，inserted=%d"
          % (len(snap_rows), len(absent_rows), res["inserted"]))


if __name__ == "__main__":
    sys.exit(main())
