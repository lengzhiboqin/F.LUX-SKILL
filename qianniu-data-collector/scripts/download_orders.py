#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
表1：订单数据下载
通过页面内fetch调用 /api/v1/orderfuse/newQueryList API
按2小时切片，分页拉取，支持每日和自定义日期
"""

import json
import sys
import time
import argparse
from pathlib import Path
from datetime import datetime, timedelta

# 添加脚本目录到路径
sys.path.insert(0, str(Path(__file__).parent))
from qn_cdp import QianniuCDP
from common import get_raw_dir, save_json, FLUX_KEYWORDS, EXCLUDE_KEYWORDS


def fetch_orders(qn, start_date, end_date):
    """
    拉取指定日期范围的订单
    按2小时切片，每页100条
    """
    js_body = r'''
    (async (startDate, endDate) => {
        const orders = [];
        let totalFetched = 0;
        let totalPages = 0;
        const start = new Date(startDate + 'T00:00:00+08:00').getTime();
        const end = new Date(endDate + 'T23:59:59+08:00').getTime();
        const SLICE = 2 * 3600 * 1000;

        for (let t0 = start; t0 < end; t0 += SLICE) {
            const t1 = Math.min(t0 + SLICE - 1, end);
            let pageNo = 1;
            while (true) {
                const resp = await fetch('/api/v1/orderfuse/newQueryList', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        pageNo, pageSize: 100, queryType: 0,
                        createStartTime: t0, createEndTime: t1,
                        orderStatusList: [], channelTypeList: [], poiIdList: []
                    })
                });
                const data = await resp.json();
                const list = (data.data && data.data.orderList) || [];
                if (list.length === 0) break;

                for (const o of list) {
                    orders.push({
                        orderId: o.orderId || o.id,
                        storeName: o.storeName,
                        storeId: o.poiId || o.storeId,
                        channelName: o.channelName,
                        createTime: o.createTime,
                        orderStatus: o.orderStatus,
                        orderStatusDesc: o.orderStatusDesc,
                        cancelStatus: o.cancelStatus,
                        actualPayAmt: o.actualPayAmt,
                        originalAmt: o.originalAmt,
                        totalPayAmount: o.totalPayAmount,
                        productCount: (o.productList || []).length,
                        productList: (o.productList || []).map(p => ({
                            productName: p.productName,
                            skuId: p.skuId,
                            spuId: p.spuId,
                            quantity: p.quantity,
                            totalPayAmount: p.totalPayAmount,
                            originalPrice: p.originalPrice
                        }))
                    });
                }
                totalFetched += list.length;
                totalPages++;

                const pi = data.data && data.data.pageInfo;
                if (!pi || pageNo >= pi.totalPage || list.length < 100) break;
                pageNo++;
            }
        }
        return JSON.stringify({totalFetched, totalPages, orderCount: orders.length, orders});
    })
    '''

    expr = f"({js_body})({json.dumps(start_date)}, {json.dumps(end_date)})"
    result = qn.js(expr, timeout=600)
    return json.loads(result)


def is_flux_store(store_name):
    """判断是否为F.LUX门店"""
    if not store_name:
        return False
    for kw in EXCLUDE_KEYWORDS:
        if kw in store_name:
            return False
    for kw in FLUX_KEYWORDS:
        if kw.lower() in store_name.lower():
            return True
    return False


def main():
    parser = argparse.ArgumentParser(description="牵牛花订单数据下载")
    parser.add_argument("--date", help="数据日期 YYYY-MM-DD，默认昨日")
    parser.add_argument("--start", help="开始日期 YYYY-MM-DD（与--end配合使用）")
    parser.add_argument("--end", help="结束日期 YYYY-MM-DD")
    parser.add_argument("--out", help="输出目录，默认 raw/<日期>/")
    args = parser.parse_args()

    # 确定日期范围
    if args.start and args.end:
        start_date, end_date = args.start, args.end
        date_label = f"{start_date}_{end_date}"
    elif args.date:
        start_date = end_date = args.date
        date_label = args.date
    else:
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        start_date = end_date = yesterday
        date_label = yesterday

    # 输出目录
    if args.out:
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
    else:
        out_dir = get_raw_dir(date_label)

    output_file = out_dir / f"订单API_{date_label}.json"

    print(f"{'='*60}")
    print(f"表1：订单数据下载")
    print(f"日期范围: {start_date} ~ {end_date}")
    print(f"输出文件: {output_file}")
    print(f"{'='*60}\n")

    t0 = time.time()
    with QianniuCDP() as qn:
        print(f"已连接: {qn.page_url}")

        print("正在拉取订单数据...")
        data = fetch_orders(qn, start_date, end_date)

    print(f"\n拉取完成:")
    print(f"  总获取条数: {data['totalFetched']}")
    print(f"  总页数: {data['totalPages']}")
    print(f"  订单数: {data['orderCount']}")

    # 过滤F.LUX门店
    all_orders = data["orders"]
    flux_orders = [o for o in all_orders if is_flux_store(o.get("storeName", ""))]
    print(f"  F.LUX门店订单: {len(flux_orders)} (剔除 {len(all_orders)-len(flux_orders)} 条跨品牌)")

    # 按门店统计
    store_stats = {}
    for o in flux_orders:
        store = o.get("storeName", "未知")
        if store not in store_stats:
            store_stats[store] = {"orders": 0, "amount": 0}
        store_stats[store]["orders"] += 1
        store_stats[store]["amount"] += (o.get("actualPayAmt", 0) or 0) / 100

    print(f"\n门店统计（Top10）:")
    sorted_stores = sorted(store_stats.items(), key=lambda x: x[1]["orders"], reverse=True)
    for store, stats in sorted_stores[:10]:
        print(f"  {store}: {stats['orders']}单, ¥{stats['amount']:.2f}")

    # 保存
    output_data = {
        "meta": {
            "table": "订单API",
            "start_date": start_date,
            "end_date": end_date,
            "fetch_time": datetime.now().isoformat(),
            "total_fetched": data["totalFetched"],
            "flux_order_count": len(flux_orders),
            "store_count": len(store_stats),
            "brand": "F.LUX（已剔除跨品牌门店：觅初）"
        },
        "orders": flux_orders
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    elapsed = time.time() - t0
    print(f"\n✅ 保存成功: {output_file}")
    print(f"⏱️  耗时: {elapsed:.1f}秒")

    return 0


if __name__ == "__main__":
    sys.exit(main())
