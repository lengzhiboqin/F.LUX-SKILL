# -*- coding: utf-8 -*-
"""牵牛花中台采集器 —— 支持任意日期区间 + 渠道拆分（MDP-TASK-102）

与 `_claude工作区/collect.py`（探索期脚本）的差别：

1. 表配置从 `lambda d: ...` 改为**静态 dict + 日期注入**，
   逐表声明 `date_field` / `date_fmt`（本项目日期字段名与格式逐表不同）
2. 新增 `--tables` / `--from` / `--to`，可只补某几张表、某个区间
3. 新增 `--channels`，支持按渠道拆分导出（ADR-0011 的前置条件）
4. **修正任务定位逻辑（R10）** —— 原来的「按名字取第一条」会拿到别的口径的文件
5. 表清单固定为 `TARGET_TABLES`（11 张），避免误采

用法：
    python scripts/qianniu_collect.py --tables 门店经营指标 --from 20260922 --to 20260922
    python scripts/qianniu_collect.py --tables 门店经营指标 --channels 100,200 --from 20260925 --to 20260925
    python scripts/qianniu_collect.py --all --from 20260801 --to 20260921
"""
import argparse
import base64
import copy
import datetime as _dt
import hashlib
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common.cdp import QN                                     # noqa: E402
from common.dates import daterange, to_compact, to_dashed      # noqa: E402
from verify_goods_flow import verify_file                     # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BASE_OUT = ROOT / "牵牛花已下载表_v2"
# 滚动表覆盖入库时，旧快照移到这里（**不删除**）
SNAPSHOT_ARCHIVE_DIR = "_废弃_旧快照"
MANIFEST = BASE_OUT / "manifest.json"

HOME = "https://qnh.meituan.com/home.html"
URL_COMMON = "/goldengateway/empower/complexModule/downloadTable"
URL_GENERIC = ("/goldengateway/empower/generic/table/download"
               "?yodaReady=h5&csecplatform=4&csecversion=4.3.0")
URL_COMPONENT = "/goldengateway/empower/component/show/downloadTable"

# ============================================================
# 卡点3：商品流量近30天「就绪点」（平台横幅：昨日数据预计 14:00 更新）
# 早于该点采集，平台不报错、静默回退窗口，给历史稀疏快照。
# 采集脚本对该表：内容比对门兜底 + 未就绪时等待到就绪点后重采。
# ============================================================
GOODS_FLOW_READY_HOUR = 14
GOODS_FLOW_READY_MIN = 5        # 就绪点 +5 分钟缓冲
GOODS_FLOW_READY_RETRIES = 4    # 就绪点后最多再轮询/重采次数
GOODS_FLOW_MIN_EXPOSURE = 100000

# ============================================================
# 渠道（ADR-0011 规则 0：牵牛花必须按渠道拆采）
# 用户 2026-09-27 确认：枚举只有 100 / 200
# ============================================================
CHANNELS = {"100": "美团闪购", "200": "饿了么/淘宝闪购"}

# 只有这些表的导出接口支持 channelIds（2026-09-27 从请求体确认）
CHANNEL_AWARE = {"门店经营指标", "品类分析", "配送指标明细"}

# ==================== 常量（程序化迁移自 _claude工作区/collect.py）====================

POI = [
    1157860,
    1157861,
    1206441,
    1281141,
    1289746,
    1203989,
    1198294,
    1219640,
    1321137,
    1312571,
    1193915,
    1327940,
    1283416,
    1324228,
    1287431,
    1213439,
    1303069,
    1292041,
    1275419,
    1196981,
    1275467,
    1290938,
    1323973,
    1315605,
    1307169,
    1215600,
    1215553,
    1283427,
    1315647,
    1338763,
    1327692,
    1322282,
    1277409,
    1316949,
    1276778,
    1274800,
    1326754,
    1231506,
    1288508,
    1314276,
    1302679,
    1307158,
    1255892,
    1269969,
    1184923,
    1215951,
    1269738,
    1269819,
    1267222,
    1267915,
]

CITY = [
    110100,
    140100,
    140900,
    150100,
    320600,
    320900,
    321000,
    330100,
    330500,
    330700,
    330800,
    350100,
    350900,
    370100,
    410300,
    430100,
    430200,
    430300,
    430700,
    440100,
    440300,
    440500,
    440600,
    440900,
    441300,
    442000,
    450300,
    450900,
    500100,
    511000,
    520100,
    520300,
    530900,
    533100,
    540100,
    610900,
    620100,
    653100,
]

FULL38 = [
    'total_prod_sale_amt',
    'all_sale_income',
    'prod_sale_amt',
    'sale_income',
    'all_actual_pay_amt',
    'refund_amt',
    'category_sale_amt_pct',
    'avg_category_sale_amt',
    'poi_prod_charge_fee',
    'poi_ord_charge_fee',
    'poi_up_charge_fee',
    'flat_prod_charge_fee',
    'flat_ord_charge_fee',
    'flat_up_charge_fee',
    'org_prod_unit_price',
    'prod_sale_num',
    'txn_sku_num',
    'non_txn_sku_num',
    'txn_sku_rate',
    'avg_prod_sale_num',
    'category_sale_num_pct',
    'refund_prod_cnt',
    'eff_ord_cnt',
    'refund_ord_cnt',
    'total_sku_num',
    'online_sku_num',
    'onsale_sku_num',
    'act_sku_num',
    'stockout_cost',
    'stockout_sku_num',
    'unsale_stock_sku_num',
    'sellout_sku_num',
    'stockout_sku_num_top80',
    'stockout_amt_top80',
    'stockout_interval_top80',
    'level_a_sku_stock_rate',
    'poi_name',
    'poi_id',
]

CATEGORY_CODES = [
    'total_prod_sale_amt',
    'all_sale_income',
    'org_poi_ord_charge_fee',
    'org_poi_up_charge_fee',
    'total_ord_cnt',
    'prod_sale_num',
    'onsale_sku_num',
    'txn_sku_num',
    'txn_sku_rate',
    'gross_profit',
    'gross_profit_margin',
    'stockout_cost',
    'first_category_name',
]

FLOW_CODES = [
    'poi_view_uv',
    'poi_enter_uv',
    'poi_ord_uv_gmv',
    'poi_ctr_uv',
    'poi_cvr_uv_gmv',
    'poi_cxr_uv_gmv',
]

RANK_CODES = [
    'poi_view_uv',
    'poi_view_pv',
    'poi_enter_uv',
    'poi_enter_pv',
    'poi_ord_uv_gmv',
    'poi_ord_cnt_gmv',
    'poi_ctr_uv',
    'poi_cvr_uv_gmv',
    'poi_cxr_uv_gmv',
]

DELIVERY_CODES = [
    'delivery_ord_cnt',
    'dlvy_except_ord_cnt',
    'delivery_complete_ord_cnt',
    'delivery_complete_rate',
    'delivery_ontime_ord_cnt',
    'delivery_ontime_rate',
    'all_timeout_ord_cnt',
    'dlvy_overtime_ord_cnt',
    'dlvy_overtime_rate',
    'dlvy_overtime_rfd_ord_cnt',
    'dlvy_overtime_rfd_amt',
    'attitude_ord_cnt',
    'attitude_rfd_ord_cnt',
    'attitude_rfd_ord_amt',
    'unrecv_15min_ord_cnt',
    'avg_delivery_distance',
    'avg_ord_weight',
    'avg_delivery_interval',
    'avg_order_all_interval',
    'avg_rider_receive_interval',
    'avg_rider_arrive_interval',
    'avg_rider_deal_interval',
    'avg_rider_delivery_interval',
    'avg_rider_take_interval',
    'poi_shipping_fee',
    'avg_poi_shipping_fee',
    'poi_shipping_base_fee',
    'avg_poi_shipping_base_fee',
    'poi_shipping_tip',
    'avg_poi_shipping_tip',
    'pfm_service_fee',
    'avg_pfm_service_fee',
]

# ==================== 表配置（程序化迁移，勿手改）====================
TABLES = {
    '门店经营指标': {
        "route":        '#/data/home/new',
        "url":          '/goldengateway/empower/generic/table/download?yodaReady=h5&csecplatform=4&csecversion=4.3.0',
        "prefix":       '导出首页门店明细',
        "date_field":   'beginDate', "date_fmt": 'compact',
        "expect_cols":  20, "expect_cols_channel": 16,
        "body":         {
            'viewCode': 'homepage_not_erp_poi_rank_table_view',
            'param': {
                'poiIds': POI,
                'channelIds': [],
                'franchiseePerspective': 0,
                'isNonSalePoi': 0,
                'page': 1,
                'pageSize': 15,
                'order': '',
                'isSelectAllPoi': True,
                'onlySelected': False,
                'statisticsType': 1,
                'dateType': 'd',
                'exportBtnId': 'homepage_not_erp_poi_rank_table_view',
            },
        },
    },
    '门店商品指标明细': {
        "route":        '#/data/product/details',
        "url":          '/goldengateway/empower/complexModule/downloadTable',
        "prefix":       '导出商品详情一级类目',
        "date_field":   'beginDate', "date_fmt": 'compact',
        "expect_cols":  38,
        "body":         {
            'viewCode': 'fusion_product_category_all_in_one_view_new',
            'param': {
                'dateType': 'd',
                'categoryType': 2,
                'isSelectAllPoi': False,
                'isSelectAllChannel': False,
                'isSelectAllCatalog': False,
                'isSelectAllFrontCategory': False,
                'dimType': 1,
                'containsCr': False,
                'containsLast': False,
                'filterCode': [],
                'needSummary': 'true',
                'order': '',
                'selectedDataCodes': [
                    'total_prod_sale_amt',
                    'all_sale_income',
                    'prod_sale_amt',
                    'sale_income',
                    'all_actual_pay_amt',
                    'refund_amt',
                    'category_sale_amt_pct',
                    'avg_category_sale_amt',
                    'poi_prod_charge_fee',
                    'poi_ord_charge_fee',
                    'poi_up_charge_fee',
                    'flat_prod_charge_fee',
                    'flat_ord_charge_fee',
                    'flat_up_charge_fee',
                    'org_prod_unit_price',
                    'prod_sale_num',
                    'txn_sku_num',
                    'non_txn_sku_num',
                    'txn_sku_rate',
                    'avg_prod_sale_num',
                    'category_sale_num_pct',
                    'refund_prod_cnt',
                    'eff_ord_cnt',
                    'refund_ord_cnt',
                    'total_sku_num',
                    'online_sku_num',
                    'onsale_sku_num',
                    'act_sku_num',
                    'stockout_cost',
                    'stockout_sku_num',
                    'unsale_stock_sku_num',
                    'sellout_sku_num',
                    'stockout_sku_num_top80',
                    'stockout_amt_top80',
                    'stockout_interval_top80',
                    'level_a_sku_stock_rate',
                    'poi_name',
                    'poi_id',
                ],
                'page': 1,
                'pageSize': 10,
                'poiIds': POI,
                'onlySelected': True,
                'exportBtnId': 'DATA_PRODUCT_DETAILS_FIRST_CATEGORY_INDICATORS_TABLE_EXPORT',
                'categoryLevel': 1,
            },
        },
    },
    '品类分析': {
        "route":        '#/data/category-analysis',
        "url":          '/goldengateway/empower/complexModule/downloadTable',
        "prefix":       '导出品类分析',
        "date_field":   'beginDate', "date_fmt": 'compact',
        "expect_cols":  11,
        "body":         {
            'viewCode': 'fusion_category_detail_table_view_v1',
            'param': {
                'poiIds': POI,
                'dateType': 'd',
                'firstCategoryIds': [],
                'secondCategoryIds': [],
                'thirdCategoryIds': [],
                'isSelectAllCatalogIds': False,
                'channelIds': [
                    '100',
                    '200',
                ],
                'categoryLevel': 1,
                'categoryType': 2,
                'isMainCategory': '1',
                'containsCr': False,
                'containsLast': False,
                'filterCode': [],
                'needSummary': True,
                'selectedDataCodes': [
                    'total_prod_sale_amt',
                    'all_sale_income',
                    'org_poi_ord_charge_fee',
                    'org_poi_up_charge_fee',
                    'total_ord_cnt',
                    'prod_sale_num',
                    'onsale_sku_num',
                    'txn_sku_num',
                    'txn_sku_rate',
                    'gross_profit',
                    'gross_profit_margin',
                    'stockout_cost',
                    'first_category_name',
                ],
                'dimType': 21,
                'page': 1,
                'pageSize': 50,
                'order': '',
                'exportBtnId': 'DATA_CATEGORY_DAILY_TABLE_EXPORT',
            },
        },
    },
    '配送指标明细': {
        "route":        '#/data/delivery/overview/new',
        "url":          '/goldengateway/empower/complexModule/downloadTable',
        "prefix":       '导出配送分析概览',
        "date_field":   'startDate', "date_fmt": 'compact',
        "expect_cols":  100,
        "body":         {
            'viewCode': 'fusion_delivery_poi_platform_data_table',
            'param': {
                'selectedDataCodes': [
                    'delivery_ord_cnt',
                    'dlvy_except_ord_cnt',
                    'delivery_complete_ord_cnt',
                    'delivery_complete_rate',
                    'delivery_ontime_ord_cnt',
                    'delivery_ontime_rate',
                    'all_timeout_ord_cnt',
                    'dlvy_overtime_ord_cnt',
                    'dlvy_overtime_rate',
                    'dlvy_overtime_rfd_ord_cnt',
                    'dlvy_overtime_rfd_amt',
                    'attitude_ord_cnt',
                    'attitude_rfd_ord_cnt',
                    'attitude_rfd_ord_amt',
                    'unrecv_15min_ord_cnt',
                    'avg_delivery_distance',
                    'avg_ord_weight',
                    'avg_delivery_interval',
                    'avg_order_all_interval',
                    'avg_rider_receive_interval',
                    'avg_rider_arrive_interval',
                    'avg_rider_deal_interval',
                    'avg_rider_delivery_interval',
                    'avg_rider_take_interval',
                    'poi_shipping_fee',
                    'avg_poi_shipping_fee',
                    'poi_shipping_base_fee',
                    'avg_poi_shipping_base_fee',
                    'poi_shipping_tip',
                    'avg_poi_shipping_tip',
                    'pfm_service_fee',
                    'avg_pfm_service_fee',
                ],
                'filterCode': [
                    'crValue',
                    'crRatio',
                ],
                'order': 'delivery_ord_cnt ',
                'dateType': 'd',
                'channelIds': [],
                'platformIds': [],
                'poiIds': POI,
                'isSelectAllPoi': False,
                'onlySelected': True,
                'isSelectAllChannel': False,
                'page': 1,
                'pageSize': 10,
                'queryType': 1,
                'exportBtnId': 'DATA_DELIVERY_OVERVIEW_INDICATORS_TABLE_EXPORT',
            },
        },
    },
    '配送距离分布': {
        "route":        '#/data/delivery/overview/new',
        "url":          '/goldengateway/empower/component/show/downloadTable',
        "prefix":       '导出配送距离分布',
        "date_field":   'startDate', "date_fmt": 'compact',
        "expect_cols":  22,
        "body":         {
            'viewCode': 'fulfillment_analyze_brief_delivery_cost_view',
            'param': {
                'dateType': 'd',
                'poiIds': POI,
                'channelIds': [],
                'platformIds': [],
                'page': 1,
                'pageSize': 50,
                'order': '',
                'filterCode': [],
                'exportBtnId': 'fulfillment_analyze_brief_delivery_cost_view',
            },
        },
    },
    '门店流量排行': {
        "route":        '#/data/product/flow-overview',
        "url":          '/goldengateway/empower/component/show/downloadTable',
        "prefix":       '导出门店流量排行',
        "date_field":   'startDate', "date_fmt": 'compact',
        "expect_cols":  10,
        "body":         {
            'viewCode': 'flow_overview_poi_rank_table',
            'param': {
                'selectedDataCodes': [
                    'poi_view_uv',
                    'poi_view_pv',
                    'poi_enter_uv',
                    'poi_enter_pv',
                    'poi_ord_uv_gmv',
                    'poi_ord_cnt_gmv',
                    'poi_ctr_uv',
                    'poi_cvr_uv_gmv',
                    'poi_cxr_uv_gmv',
                ],
                'cityIdList': CITY,
                'dateType': 'd',
                'queryType': '2',
                '_realDateType': 'd',
                'containsCr': False,
                'containsLast': False,
                'comparisonType': 'onlyCurrent',
                'page': 1,
                'pageSize': 10,
                'order': '',
                'filterCode': [],
                'exportBtnId': 'flow_overview_poi_rank_table',
            },
        },
    },
    '商品流量_近30天': {
        "route":        '#/data/product/flow-performance',
        "url":          URL_COMPONENT,
        "prefix":       '导出商品流量分析',
        "rolling_days": 30,
        "date_field":   'startDate', "date_fmt": 'compact',
        "expect_cols":  30,
        "body":         {
            'viewCode': 'goods_flow_analysis_goods_list',
            'param': {
                'page': 1,
                'pageSize': 10,
                'categoryType': '2',
                'sourceType': '1',
                'dateType': 'c',
                'queryType': '1',
                '_realDateType': 30,
                'firstCategoryIds': [],
                'secondCategoryIds': [],
                'thirdCategoryIds': [],
                'order': '',
                'exportBtnId': 'PRODUCT_FLOW_PERFORMANCE_TABLE_EXPORT',
                'filterCode': [
                    'crValue',
                    'crRatio',
                ],
            },
        },
    },
    '门店流量_分来源': {
        "route":        '#/data/product/flow-overview',
        "url":          '/goldengateway/empower/component/show/downloadTable',
        "prefix":       '导出流量分来源数据',
        "date_field":   'startDate', "date_fmt": 'compact',
        "expect_cols":  7,
        "body":         {
            'viewCode': 'flow_overview_drill_down_table',
            'param': {
                'analyzeDim': 'CHANNEL',
                'selectedDataCodes': [
                    'poi_view_uv',
                    'poi_enter_uv',
                    'poi_ord_uv_gmv',
                    'poi_ctr_uv',
                    'poi_cvr_uv_gmv',
                    'poi_cxr_uv_gmv',
                ],
                'cityIdList': CITY,
                'dateType': 'd',
                'queryType': '2',
                '_realDateType': 'd',
                'containsCr': False,
                'containsLast': False,
                'comparisonType': 'onlyCurrent',
                'page': 1,
                'pageSize': 10,
                'order': '',
                'filterCode': [],
                'exportBtnId': 'flow_overview_drill_down_table_channel',
            },
        },
    },
    '商品流量_店内': {
        "route":        '#/data/product/flow-performance',
        "url":          URL_COMPONENT,
        "prefix":       '导出商品流量分析',
        "date_field":   'startDate', "date_fmt": 'compact',
        "expect_cols":  30,
        "body":         {
            'viewCode': 'goods_flow_analysis_goods_list',
            'param': {
                'page': 1,
                'pageSize': 10,
                'categoryType': '2',
                'sourceType': '2',
                'dateType': 'd',
                'queryType': '1',
                '_realDateType': '1',
                'firstCategoryIds': [],
                'secondCategoryIds': [],
                'thirdCategoryIds': [],
                'order': '',
                'exportBtnId': 'PRODUCT_FLOW_PERFORMANCE_TABLE_EXPORT',
                'filterCode': [
                    'crValue',
                    'crRatio',
                ],
            },
        },
    },
    '商品流量_店外': {
        "route":        '#/data/product/flow-performance',
        "url":          URL_COMPONENT,
        "prefix":       '导出商品流量分析',
        "date_field":   'startDate', "date_fmt": 'compact',
        "expect_cols":  30,
        "body":         {
            'viewCode': 'goods_flow_analysis_goods_list',
            'param': {
                'page': 1,
                'pageSize': 10,
                'categoryType': '2',
                'sourceType': '3',
                'dateType': 'd',
                'queryType': '1',
                '_realDateType': '1',
                'firstCategoryIds': [],
                'secondCategoryIds': [],
                'thirdCategoryIds': [],
                'order': '',
                'exportBtnId': 'PRODUCT_FLOW_PERFORMANCE_TABLE_EXPORT',
                'filterCode': [
                    'crValue',
                    'crRatio',
                ],
            },
        },
    },
    '门店营业时长': {
        "route":        '#/data/store-hours',
        "url":          URL_GENERIC,
        "prefix":       '导出门店营业时长',
        "date_field":   'startDate', "date_fmt": 'dashed',
        "expect_cols":  16,
        "body":         {
            'viewCode': 'qnh_store_operating_hours_header_new',
            'param': {
                'order': 'avg_poi_rest_dur asc',
                'regionCodes': POI,
                'isSelectAllPoi': True,
                'isSelectAllChannel': True,
                'exportBtnId': 'DATA_STORE_HOURS_TABLE_EXPORT',
                'page': 1,
                'pageSize': 10,
            },
        },
    },
}

TARGET_TABLES = list(TABLES)


# ---------------- 纯函数（可独立测试） ----------------

def rolling_window(day: str, days: int) -> tuple:
    """滚动窗口的 `(起始, 结束)`（均紧凑型）：`[day-days, day-1]`。

    ⚠️ **结束日必须是「昨天」**（`day-1`），不能是 `day` —— 实测：
    区间对不上时平台**静默退回「按日」**，不报错，但给的是单日数据。
    依据：[数据字典](../10-事实基准/数据字典.md) 第 8 行。
    """
    c = to_compact(day)
    d0 = _dt.date(int(c[:4]), int(c[4:6]), int(c[6:8]))
    return ((d0 - _dt.timedelta(days=days)).strftime("%Y%m%d"),
            (d0 - _dt.timedelta(days=1)).strftime("%Y%m%d"))


def build_body(table: str, day: str, channel: str | None = None) -> dict:
    """基于模板生成请求体。**深拷贝**，不污染模板。

    两类表：

    | 类型 | 判定 | 日期参数 |
    |---|---|---|
    | **单日表**（10 张） | 无 `rolling_days` | 起始 = 结束 = `day`（数据日） |
    | **滚动表**（`商品流量_近30天`） | 有 `rolling_days` | 起始 = `day-days`，结束 = `day-1`（`day` 是**提交日**） |

    对滚动表，`day` 是**提交日**（采集日），不是数据日 —— 它的窗口永远相对提交日。
    见 [表结构核对表](../10-事实基准/表结构核对表.md) §5.1。

    :param channel: 渠道 ID（如 "100"）。仅 `CHANNEL_AWARE` 里的表生效；
                    其余表**忽略**该参数（不报错，由 main 打印提示）。
    """
    cfg = TABLES[table]
    body = copy.deepcopy(cfg["body"])
    dashed = cfg["date_fmt"] == "dashed"
    fmt = to_dashed if dashed else to_compact

    if cfg.get("rolling_days"):
        start_c, end_c = rolling_window(day, cfg["rolling_days"])
    else:
        start_c = end_c = to_compact(day)

    body["param"][cfg["date_field"]] = fmt(start_c)
    body["param"]["endDate"] = fmt(end_c)

    if channel is not None and table in CHANNEL_AWARE:
        body["param"]["channelIds"] = [channel]
    return body


def unique_path(path):
    """返回一个**不与现有文件冲突**的路径：`名__2.ext`、`名__3.ext`…

    ⚠️ 为什么需要（复审 B1-b）：归档时若同名文件已存在，
    **既不能覆盖、也不能删除** —— 任务书 §四第 4 条「不得删除任何已有文件」。
    原实现先 `os.remove(dest)` 再 move，等于删掉了已有归档。
    """
    p = Path(path)
    if not p.exists():
        return p
    n = 2
    while True:
        cand = p.with_name(f"{p.stem}__{n}{p.suffix}")
        if not cand.exists():
            return cand
        n += 1


def superseded_snapshots(table_dir, table: str, keep_name: str) -> list:
    """滚动表里**除最新一份外**的旧快照（要移入归档，**不删除**）。

    ⚠️ 滚动表是**覆盖入库**（只留最新一份）：窗口永远相对采集日，没有历史价值。
    依据：[表结构核对表](../10-事实基准/表结构核对表.md) §5.2。
    归档而非物理删除 —— 与项目「清理一律用迁移/备份代替」的约定一致。

    :param keep_name: 本次新采的文件名（保留它）。
    """
    out = []
    for p in Path(table_dir).glob(f"{table}_*.xlsx"):
        if "_ch" in p.stem:
            continue
        tail = p.stem.rsplit("_", 1)[1]
        if len(tail) == 8 and tail.isdigit() and p.name != keep_name:
            out.append(p)
    return sorted(out)


def build_jobs(tables: list, dates: list, channels: list, today: str) -> list:
    """把「表 × 日期 × 渠道」展开成采集任务列表。

    ⚠️ **滚动表（快照）每次运行只采一次**，归属日 = **采集日**（`today`）。

    为什么必须特殊处理：滚动表的区间**相对提交日**，与传入的日期无关。
    若照常按区间展开，传 56 天就会对它**提交 56 次、下载 56 份完全相同的文件**
    —— 这正是 2026-09-27 出错的那类 bug。

    :return: `[(表, 归属日, 渠道 or None), ...]`
    """
    jobs = []
    for t in tables:
        chs = channels if (channels and t in CHANNEL_AWARE) else [None]
        days = [today] if TABLES[t].get("rolling_days") else dates
        jobs += [(t, d, ch) for d in days for ch in chs]
    return jobs


def task_suffix(table: str, day: str, channel: str | None = None) -> str:
    """任务名的日期后缀，用于在任务中心定位本次提交的任务。

    ⚠️ **任务名后缀一律是紧凑型** —— 与请求体里的日期格式无关。
    实测（`manifest.json` 里的真实任务名）：
        门店营业时长（请求体用 `2026-08-18`）→ 任务名 `..._20260818`
        其余各表（请求体用 `20260801`）      → 任务名 `..._20260801`
    两种都是紧凑型。**不能拿请求体的日期格式当任务名后缀。**

    ⚠️ **唯一的例外是滚动表**：它的后缀是 `起始-结束`两段。
    实测（2026-09-27 任务中心实读）：
        `导出商品流量分析20260927_20260828-20260926`
    提交日 20260927 → `[提交日-30, 提交日-1]`。**后缀跟着区间走**。

    ⚠️ **不要往里加渠道标记** —— 任务名是**平台生成的**，格式固定为
    `导出<报表名><提交日>_<数据日>`，不接受我们自定义的任何后缀。
    加了标记会永远匹配不上（实测：平台给的任务名里没有渠道信息）。

    那同一天两个渠道的任务怎么区分？—— **靠「提交前基线」**：
    每次提交前记录当前任务 id 集合，提交后只认「新出现的」那条。
    两条渠道先后提交时，第二次的基线已包含第一次的任务，所以不会拿错。
    （[ADR-0011] 规则 0 的前置实现）

    :param channel: 仅为签名对称保留，**不影响返回值**。
    """
    cfg = TABLES[table]

    if cfg.get("rolling_days"):
        # 滚动表：后缀是 `起始-结束`（均为紧凑型）
        start, end = rolling_window(day, cfg["rolling_days"])
        return f"{start}-{end}"

    return to_compact(day)                      # ← 始终紧凑型


def pick_latest_new_task(tasks: list, prefix: str, day: str,
                         baseline=(), suffix: str | None = None):
    """从任务列表里挑出「**本次提交之后新建的**最新一条」。找不到返回 None。

    ⚠️ R10：同一 (报表, 日期) 可以并存多个**同名**任务（不同渠道/口径各建一个）。
    按名字取第一条会拿到别的口径的文件。所以两个条件缺一不可：
      1. **只认不在 baseline 里的**（= 提交之后新建的）
      2. 多条时取 **taskId 最大**（= 最新的）
    """
    want = f"_{suffix or day}"
    baseline = {str(b) for b in baseline}
    cands = [t for t in tasks
             if t["n"].startswith(prefix) and t["n"].endswith(want)
             and str(t["id"]) not in baseline]
    if not cands:
        return None
    latest = max(cands, key=lambda t: int(t["id"]))
    return latest if latest["st"] in ("已完成", "执行失败", "无数据") else None


def check_file(path, table: str, channel: str | None = None):
    """采集后校验：**列数**必须与配置一致。

    为什么必须校验：平台的导出结果会因参数变化而**改列数**。
    实测（2026-09-27）：门店经营指标按渠道筛选后从 19 列掉到 16 列 ——
    少了「净利润（账单口径）」「线下其他」「库存周转天数(金额)」三列。
    不校验的话，会把「少了 3 列的数据」当成正常数据入库。

    :return: (通过?, 说明)
    """
    import openpyxl
    cfg = TABLES[table]
    want = (cfg.get("expect_cols_channel") if channel else None) or cfg["expect_cols"]
    if not want:
        return True, "未配置期望列数，跳过校验"
    try:
        ws = openpyxl.load_workbook(path, read_only=False, data_only=True).active
        got, rows = ws.max_column, ws.max_row
    except Exception as e:
        return False, f"打不开文件: {type(e).__name__}"
    if got != want:
        return False, f"列数 {got} != 期望 {want}（行数 {rows}）"
    return True, f"{rows}×{got}"



class Collector:
    def __init__(self):
        self.qn = QN()
        self.qn.dismiss_modal()
        self.manifest = self._load_manifest()
        self._cur_route = None

    def _load_manifest(self):
        os.makedirs(BASE_OUT, exist_ok=True)
        if os.path.exists(MANIFEST):
            return json.load(open(MANIFEST, encoding="utf-8"))
        return {}

    def _save_manifest(self):
        json.dump(self.manifest, open(MANIFEST, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)

    def _key(self, table, d, channel=None):
        return f"{table}|{d}" + (f"|ch{channel}" if channel else "")

    def ensure_route(self, route):
        if self._cur_route == route:
            return
        self.qn.navigate(HOME + route, self.qn.session, wait=11)
        self.qn.dismiss_modal(self.qn.session)
        self._cur_route = route

    def submit(self, url, body):
        js = f'''(async () => {{
          const r = await fetch({json.dumps(url)}, {{method:'POST', credentials:'include',
            headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({json.dumps(body)})}});
          return r.status + '|' + (await r.text()).slice(0,200);
        }})()'''
        return self.qn.js(js, self.qn.session)

    def tasks(self, n=10):
        js = f'''(async () => {{
          const r = await fetch('/api/v1/task/queryTasks', {{method:'POST',
            headers:{{'Content-Type':'application/json'}},
            body: JSON.stringify({{pageNo:1, pageSize:{n}, queryType:'TAB_DOWNLOAD'}})}});
          const j = await r.json();
          return JSON.stringify(((j.data&&j.data.list)||[]).map(t => ({{
            id:t.taskId, st:t.executingState, n:t.taskName}})));
        }})()'''
        return self.qn.jsj(js, self.qn.session)

    def task_ids(self, n=25) -> set:
        """当前任务中心里所有任务的 id 集合 —— 用于算「提交前基线」。"""
        return {str(t["id"]) for t in self.tasks(n)}

    def find_task(self, prefix, d, tries=30, gap=4, suffix=None, baseline=()):
        """找到「**本次提交之后新建的**」任务并等它完成。

        ⚠️ R10：必须传 baseline。同名任务并存时，只按名字匹配会拿到
        别的口径的文件（Phase 0 实测踩到过）。
        """
        for _ in range(tries):
            hit = pick_latest_new_task(self.tasks(25), prefix, d,
                                       baseline=baseline, suffix=suffix)
            if hit:
                return hit
            time.sleep(gap)
        return None

    def download(self, task_id, path):
        js = f'''(async () => {{
          const r = await fetch("/api/v1/task/exportData?taskId={task_id}&taskType=TAB_DOWNLOAD",
                                {{credentials:'include'}});
          if (!r.ok) return 'HTTP ' + r.status;
          const b = await r.blob();
          return await new Promise(res => {{const fr=new FileReader();
            fr.onload=()=>res(fr.result.split(",")[1]); fr.readAsDataURL(b);}});
        }})()'''
        v = self.qn.js(js, self.qn.session)
        if not isinstance(v, str) or v.startswith("JS异常") or v.startswith("HTTP"):
            return None, str(v)[:200]
        data = base64.b64decode(v)
        open(path, "wb").write(data)
        return hashlib.md5(data).hexdigest()[:12], len(data)

    def run_one(self, table, d, verbose=True, channel=None):
        cfg = TABLES[table]
        k = self._key(table, d, channel)
        rec = self.manifest.get(k)
        if rec and rec.get("status") == "ok":
            if verbose:
                print(f"  [跳过] {table} {d}（已完成）")
            return rec

        self.ensure_route(cfg["route"])
        # ⚠️ R10：提交前记录任务 id 基线，只认提交之后新建的任务
        baseline = self.task_ids(25)
        req_body = build_body(table, d, channel)
        resp = self.submit(cfg["url"], req_body)
        time.sleep(3)
        self.qn.dismiss_modal(self.qn.session)

        # 提交完立刻离开数据页，去「任务中心」这种轻页面轮询。
        # 否则大表格（商品流量近30天 ~2800 行）会把 JS 主线程占死，Runtime.evaluate 直接超时。
        # 先跳 about:blank 把重页面彻底卸掉，再进任务中心 —— 否则重型页面卸载过程本身就可能卡住渲染线程。
        try:
            self.qn.call("Page.navigate", {"url": "about:blank"}, self.qn.session, timeout=30)
            time.sleep(1.5)
        except Exception:
            pass
        self.ensure_route("#/operation/task?queryType=TAB_DOWNLOAD")
        task = self.find_task(cfg["prefix"], d, baseline=baseline,
                              suffix=task_suffix(table, d, channel))
        rec = {"table": table, "date": d, "submit": resp, "channel": channel,
               "request_body": req_body}
        if not task:
            rec["status"] = "任务未找到"
            self.manifest[k] = rec; self._save_manifest()
            if verbose:
                print(f"  [失败] {table} {d} 任务未出现  ({resp[:60]})")
            return rec
        rec["taskId"] = task["id"]
        rec["taskName"] = task["n"]
        if task["st"] != "已完成":
            rec["status"] = task["st"]
            self.manifest[k] = rec; self._save_manifest()
            if verbose:
                print(f"  [失败] {table} {d} 任务状态={task['st']}  {task['n']}")
            return rec

        outdir = os.path.join(BASE_OUT, table)
        os.makedirs(outdir, exist_ok=True)
        fname = os.path.join(outdir, f"{table}_{d}"
                              + (f"_ch{channel}" if channel else "") + ".xlsx")
        h, size = self.download(task["id"], fname)
        if h is None:
            rec["status"] = "下载失败"; rec["detail"] = size
            self.manifest[k] = rec; self._save_manifest()
            print(f"  [失败] {table} {d} 下载失败: {size}")
            return rec
        # ⚠️ 返工 B3：下载后必须校验列数 —— 不合格**不能**标 ok。
        # 平台会因参数变化改列数（实测：门店经营指标按渠道筛选后 19→16 列）
        passed, detail = check_file(fname, table, channel)
        rec["file"] = fname
        rec["size"] = size
        rec["md5"] = h
        if not passed:
            # 只删除**本次刚下载**的异常文件（不是用户的历史数据）
            rec["status"] = "列数异常"
            rec["detail"] = detail
            self.manifest[k] = rec
            self._save_manifest()
            print(f"  [失败] {table} {d} 列数校验不通过: {detail}")
            return rec
        # 卡点3：商品流量近30天额外跑「内容级比对门」（稀疏件也是30列，列数门拦不住）
        if table == "商品流量_近30天":
            expected_end = rolling_window(d, cfg["rolling_days"])[1]
            prev_file = self._find_prev_snapshot(table, fname)
            verdict, vout = verify_file(fname, expected_end,
                                        task_name=task["n"],
                                        prev_file=prev_file,
                                        min_exposure=GOODS_FLOW_MIN_EXPOSURE)
            rec["content_verdict"] = verdict
            rec["content_failed_gates"] = vout.get("failed_gates")
            rec["content_fingerprint"] = vout["fingerprint"]
            if verdict != "PASS":
                rec["status"] = "数据未就绪"
                rec["detail"] = vout.get("failed_gates")
                self.manifest[k] = rec
                self._save_manifest()
                print(f"  [未就绪] {table} {d} 内容比对未过: "
                      f"{vout.get('failed_gates')}（应于14点后重采）")
                return rec
        rec["status"] = "ok"
        rec["cols"] = detail
        # ⚠️ 滚动表：**覆盖入库**，把上一份快照移到归档目录（不删除）
        if TABLES[table].get("rolling_days"):
            self._supersede_snapshots(table, fname)
        self.manifest[k] = rec
        self._save_manifest()
        if verbose:
            print(f"  [完成] {table} {d}  {size} 字节  ← {task['n']}")
        return rec

    def _ready_point(self, day):
        """商品流量就绪点的本地时间（提交日当天的 14:05）。"""
        c = to_compact(day)
        return _dt.datetime(int(c[:4]), int(c[4:6]), int(c[6:8]),
                            GOODS_FLOW_READY_HOUR, GOODS_FLOW_READY_MIN)

    def run_goods_flow_ready(self, day, wait_ready=True, verbose=True):
        """采集商品流量近30天；若未就绪，等待到就绪点后自动重采（不把稀疏件入库）。"""
        rec = self.run_one("商品流量_近30天", day, verbose=verbose)
        if rec.get("status") == "ok" or not wait_ready:
            return rec
        for attempt in range(1, GOODS_FLOW_READY_RETRIES + 1):
            wait_s = (self._ready_point(day) - _dt.datetime.now()).total_seconds()
            if wait_s > 0:
                print(f"  [等待] 商品流量未就绪，{wait_s/60:.0f}分钟后"
                      f"（{self._ready_point(day):%H:%M}）重采", flush=True)
                end = time.time() + wait_s
                while time.time() < end:
                    time.sleep(min(30, end - time.time()))
            print(f"  [重采] 第 {attempt} 次", flush=True)
            rec = self.run_one("商品流量_近30天", day, verbose=verbose)
            if rec.get("status") == "ok":
                return rec
            time.sleep(600)   # 就绪点后仍未就绪，10分钟后再试
        return rec

    def _find_prev_snapshot(self, table, exclude_file):
        """找当前目录（或归档目录）里除本次外的最近一份快照，用于环比门；无则空串。"""
        tdir = os.path.join(BASE_OUT, table)
        cands = []
        for d in (tdir, os.path.join(tdir, SNAPSHOT_ARCHIVE_DIR)):
            if not os.path.isdir(d):
                continue
            for p in os.listdir(d):
                fp = os.path.join(d, p)
                if (os.path.isfile(fp) and fp != exclude_file
                        and p.endswith(".xlsx") and "_ch" not in p):
                    cands.append((os.path.getmtime(fp), fp))
        return sorted(cands)[-1][1] if cands else ""

    def _supersede_snapshots(self, table, new_path):
        """滚动表覆盖入库：旧快照移入 `_废弃_旧快照/`，并修正 manifest 里的路径。

        **不删除** —— 项目约定「清理一律用迁移代替物理删除」。
        """
        old = superseded_snapshots(os.path.dirname(new_path), table,
                                   os.path.basename(new_path))
        if not old:
            return
        adir = os.path.join(os.path.dirname(new_path), SNAPSHOT_ARCHIVE_DIR)
        os.makedirs(adir, exist_ok=True)
        moved = {}
        for p in old:
            dest = unique_path(os.path.join(adir, p.name))
            # ⚠️ 撞名时改存 `__N` 后缀，**绝不覆盖、绝不删除**已有归档（复审 B1-b）
            if Path(dest).name != p.name:
                print(f"   [归档] 同名已存在，改存 {Path(dest).name}"
                      f"（原有文件未动）", flush=True)
            os.replace(p, dest)
            moved[str(p)] = str(dest)
            print(f"   [覆盖] 旧快照已归档（未删除）: {p.name}", flush=True)
        changed = False
        for rec in self.manifest.values():
            f = rec.get("file")
            if f and f in moved:
                rec["file"] = moved[f]
                rec["superseded_by"] = os.path.basename(new_path)
                changed = True
        if changed:
            self._save_manifest()

    def reconnect(self):
        """CDP 卡死/断开后重建连接，并确认页面真的能响应"""
        try:
            self.qn.close()
        except Exception:
            pass
        time.sleep(3)
        self.qn = QN()
        self._cur_route = None
        # 健康检查：标签页渲染线程卡死时 evaluate 会超时，这时只能重建标签页
        for i in range(4):
            try:
                if self.qn.js("1+1", timeout=25) == 2:
                    print("   [重连] CDP 连接已重建，页面响应正常")
                    return
            except Exception:
                pass
            print(f"   [重建标签页] 第 {i + 1} 次（原标签页无响应）", flush=True)
            try:
                self.qn.rebuild_tab()
            except Exception as e:
                print(f"     重建失败: {type(e).__name__}", flush=True)
                time.sleep(5)
        raise RuntimeError("重连后页面仍无响应")

    def close(self):
        self.qn.close()


def main():
    ap = argparse.ArgumentParser(description="牵牛花采集器（支持日期区间 + 渠道拆分）")
    ap.add_argument("--tables", nargs="*", default=None,
                    help=f"表名，空格分隔。默认全部 {len(TARGET_TABLES)} 张")
    ap.add_argument("--all", action="store_true", help="等价于 --tables 全部")
    ap.add_argument("--from", dest="dfrom", default="20260801")
    ap.add_argument("--to", dest="dto", default="20260921")
    ap.add_argument("--channels", default="",
                    help='渠道 ID，逗号分隔（如 "100,200"）。仅对支持的表生效')
    ap.add_argument("--gap", type=float, default=3.0, help="每次提交间隔秒")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不实际采集")
    ap.add_argument("--wait-ready", action="store_true",
                    help="商品流量近30天未就绪时，等待到14:05后自动重采（卡点3）")
    args = ap.parse_args()

    tables = TARGET_TABLES if (args.all or not args.tables) else args.tables
    bad = [t for t in tables if t not in TABLES]
    if bad:
        print(f"❌ 未知表名: {bad}")
        print(f"   可选: {TARGET_TABLES}")
        return 2

    channels = [c.strip() for c in args.channels.split(",") if c.strip()]
    for ch in channels:
        if ch not in CHANNELS:
            print(f"❌ 未知渠道: {ch}（可选 {list(CHANNELS)}）")
            return 2

    if channels:
        ignored = [t for t in tables if t not in CHANNEL_AWARE]
        if ignored:
            print(f"ℹ️  以下表不支持渠道拆分，将忽略 --channels：{ignored}")

    dates = daterange(args.dfrom, args.dto)

    # 展开成 (表, 归属日, 渠道) 的任务列表（滚动表只展开一次，用采集日）
    today = _dt.date.today().strftime("%Y%m%d")
    jobs = build_jobs(tables, dates, channels, today)
    ch_desc = f" × {len(channels)} 渠道" if channels else ""
    snaps = [t for t in tables if TABLES[t].get("rolling_days")]
    print(f"计划采集 {len(jobs)} 项：{len(tables)} 表 × {len(dates)} 天{ch_desc}")
    print(f"日期区间 {dates[0]} ~ {dates[-1]}  输出目录 {BASE_OUT}")
    if snaps:
        print(f"ℹ️  滚动表（按采集日采 1 次，不按区间）：{snaps} → 归属日 {today}")

    if args.dry_run:
        for t, d, ch in jobs[:10]:
            print(f"   · {t} {d}" + (f" ch{ch}" if ch else ""))
        if len(jobs) > 10:
            print(f"   … 其余 {len(jobs) - 10} 项")
        return 0

    c = Collector()
    ok = fail = 0
    try:
        for n, (table, day, ch) in enumerate(jobs, 1):
            label = f"{table} {day}" + (f" ch{ch}" if ch else "")
            print(f"[{n}/{len(jobs)}] {label}", flush=True)
            try:
                if table == "商品流量_近30天" and args.wait_ready:
                    rec = c.run_goods_flow_ready(day, wait_ready=True)
                else:
                    rec = c.run_one(table, day, channel=ch)
            except Exception as e:
                print(f"   [异常] {type(e).__name__}: {str(e)[:150]}", flush=True)
                try:
                    c.reconnect()
                    rec = c.run_one(table, day, channel=ch)
                except Exception as e2:
                    print(f"   [重试仍失败] {type(e2).__name__}，跳过", flush=True)
                    rec = {"status": "异常"}
            ok += (rec.get("status") == "ok")
            fail += (rec.get("status") != "ok")
            time.sleep(args.gap)
    finally:
        try:
            c.close()
        except Exception:
            pass
        print(f"\n完成：成功 {ok} / 失败 {fail}（共 {len(jobs)}）")
        if MANIFEST.exists():
            man = json.loads(MANIFEST.read_text(encoding="utf-8"))
            print(f"manifest 条目 {len(man)}")
        print(f"输出目录 {BASE_OUT}")


if __name__ == "__main__":
    sys.exit(main() or 0)
