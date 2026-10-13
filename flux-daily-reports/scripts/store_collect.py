# -*- coding: utf-8 -*-
"""门店商品全量采集器（路径B）：提交异步导出 → 任务中心轮询 → 下载全量 xlsx。

## 链路（均在 qnh.meituan.com 域）

1. 提交：`POST /qnh-gw3/api/product/store/export`
   - exportType=1、tabType=1、poiId=null（全门店）、status=1
   - dataList 为 67 个字段 key（导出后展开为 88 列，其中 cartonMeasure 展开为
     货品单位/转换比例各 10 列）
2. 轮询：`POST /api/v1/task/queryTasks`
   - taskMode='infTask'、queryType='INF_TASK_$$DF6Jeb$$STORE_SPU_EXPORT_V2'
   - 靠「提交前任务 id 基线」只认本次新建的任务
3. 下载：任务 `oprList[0].url`（S3 预签名 URL，约 90 天有效），直连 s3plus.sankuai.com

## 实测（2026-10-13）

- 全量 157,888 行 × 88 列、68.4 MB、约 13 分钟、无截断、50 店

用法：
    python scripts/store_collect.py --out _data/store_raw --run-day 2026-10-13
"""
import argparse
import datetime as _dt
import json
import os
import shutil
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common.cdp import QN  # noqa: E402

HOME = "https://qnh.meituan.com/home.html"
URL_EXPORT = "/qnh-gw3/api/product/store/export"
QUERY_TYPE = "INF_TASK_$$DF6Jeb$$STORE_SPU_EXPORT_V2"

DATA_LIST = [
    "storeId", "storeName", "provinceName", "cityName", "spuId", "spuName",
    "skuId", "skuSaleType", "upc", "onlineSpec", "mtSaleAttr", "elmSaleAttr",
    "onlineStock", "CHANNEL_SELL_OUT_FLAG", "frontCategoryName", "onlineStatus",
    "mtAfterSaleService", "saleStatus", "liveStatus", "isManualOffShelf",
    "manualOffShelfTime", "manualOffShelfOperator", "hasPicture",
    "isManualOffline", "manualOfflineTime", "manualOfflineOperator",
    "sellOutOffShelf", "sellOutShelfTime", "onlineWeight", "weightUnit",
    "mtFirstCategoryName", "mtSecondCategoryName", "mtThirdCategoryName",
    "elemFirstCategoryName", "elemSecondCategoryName", "elemThirdCategoryName",
    "minOrderCount", "productionDate", "expirationDate", "expirationStatus",
    "elemChannelSellPrice", "mtChannelSellPrice", "backendCategory",
    "abnormalDesc", "abnormalHandleMsg", "childSku", "purchasePrice",
    "customSkuId", "mtCustomSpuId", "mtRealChannelSpuId", "externalCode",
    "sellPoint", "isSpecialty", "availableTime", "description", "properties",
    "brand", "monthSaleAmount", "entityStock", "mtAuditStatus",
    "mtNormAuditStatus", "mtAuditComment", "cartonMeasure", "customCode",
    "spuProductLabel", "skuProductLabel", "saleChannels",
]


def export_body():
    return {
        "backendCategoryIdList": [], "frontCategoryIdList": [], "tabType": 1,
        "spuIdList": [], "skuIdList": [], "erpCodeList": [], "upcList": [],
        "erpUpcList": [], "goodsUpcList": [], "externalCodeList": [],
        "childSkuIdList": [], "poiId": None, "saleStatusList": [], "page": 1,
        "asyncQueryPromotion": True, "multiChannelOnlineStatusQueries": [],
        "multiHasStockAutoOnShelfQueries": [], "multiSaleStatusQueries": [],
        "activityChannelId": 0, "priceSource": 0, "mtAllowSaleStatus": 0,
        "status": 1, "pageSize": 20,
        "channelCategory": {"channelId": 100, "categoryProperty": {}},
        "saleStockFlag": 0, "exportType": 1, "dataList": DATA_LIST,
        "customCodeList": [], "channelStockStatusList": [],
    }


def query_body(start, end):
    return {
        "queryType": QUERY_TYPE, "searchOpUser": "",
        "date": [start, end], "pageSize": 20,
        "startTime": start.replace("-", ""), "endTime": end.replace("-", ""),
        "page": 1, "taskMode": "infTask",
    }


def js_post(qn, path, body, j=True):
    js = """(async function(){
      const r=await fetch(%s,{method:'POST',credentials:'include',
        headers:{'Content-Type':'application/json'},body:JSON.stringify(%s)});
      const t=await r.text();
      return '%s|'+r.status+'|'+t;
    })()""" % (json.dumps(path), json.dumps(body), path)
    v = qn.js(js, timeout=120)
    return v


def current_tasks(qn, start, end):
    v = js_post(qn, "/api/v1/task/queryTasks", query_body(start, end))
    try:
        payload = v.split("|", 2)[2]
        j = json.loads(payload)
        return j["data"]["list"]
    except Exception:
        return []


def task_ids(tasks):
    return {str(t["taskId"]) for t in tasks}


def download_s3(url, dest):
    # S3 直连；不使用代理（国内 s3plus）
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=300) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f, length=1 << 20)
    return os.path.getsize(dest)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--run-day", default=None)
    ap.add_argument("--timeout", type=int, default=1800)
    args = ap.parse_args()

    run_day = args.run_day or _dt.date.today().isoformat()
    os.makedirs(args.out, exist_ok=True)

    qn = QN()
    qn.dismiss_modal()
    qn.navigate(HOME + "#/unifiedGoods/store/spu-list", wait=10)
    qn.dismiss_modal()

    # 近7天查询窗口（用于定位本次任务）
    d0 = _dt.date.fromisoformat(run_day)
    start = (d0 - _dt.timedelta(days=7)).isoformat()
    end = run_day

    baseline = task_ids(current_tasks(qn, start, end))
    print("提交前任务基线:", len(baseline))
    resp = js_post(qn, URL_EXPORT, export_body())
    print("提交响应:", str(resp)[:120])

    deadline = time.time() + args.timeout
    hit = None
    while time.time() < deadline:
        for t in current_tasks(qn, start, end):
            if str(t["taskId"]) in baseline:
                continue
            if t["executingState"] == "已完成":
                hit = t
                break
        if hit:
            break
        time.sleep(10)
        print("  等待导出完成...", flush=True)

    if not hit:
        print("TIMEOUT: 导出未在 %ds 内完成" % args.timeout)
        return 1

    url = hit["oprList"][0]["url"]
    print("任务完成:", hit["taskId"], "成功数:", hit["taskSuccessNum"])
    dest = os.path.join(args.out, "门店商品全量_%s.xlsx" % run_day.replace("-", ""))
    size = download_s3(url, dest)
    print("下载完成:", dest, "%.1f MB" % (size / 1e6))

    meta = {"run_day": run_day, "task_id": hit["taskId"],
            "success_num": hit["taskSuccessNum"], "file": dest,
            "size": size, "url": url}
    json.dump(meta, open(os.path.join(args.out, "门店商品_采集清单.json"), "w",
                         encoding="utf-8"), ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
