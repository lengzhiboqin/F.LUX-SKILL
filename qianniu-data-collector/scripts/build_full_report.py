#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
牵牛花数据采集 - 完整数据整合与Excel报告生成
整合订单API、门店经营指标、商品销售明细等多数据源，生成多Sheet Excel成品
"""

import json
import logging
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timedelta

try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
except ImportError:
    print("需要安装 openpyxl: pip install openpyxl")
    raise


# 样式定义
HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)
HEADER_FILL = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
TITLE_FONT = Font(bold=True, size=14)
SUBTITLE_FONT = Font(bold=True, size=11, color="4472C4")
CENTER_ALIGN = Alignment(horizontal="center", vertical="center")
LEFT_ALIGN = Alignment(horizontal="left", vertical="center")
THIN_BORDER = Border(
    left=Side(style='thin'), right=Side(style='thin'),
    top=Side(style='thin'), bottom=Side(style='thin')
)
WARN_FILL = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
GOOD_FILL = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")


def style_header(ws, row, cols):
    for col in range(1, cols + 1):
        cell = ws.cell(row=row, column=col)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = CENTER_ALIGN
        cell.border = THIN_BORDER


def style_data(ws, start_row, end_row, cols):
    for row in range(start_row, end_row + 1):
        for col in range(1, cols + 1):
            cell = ws.cell(row=row, column=col)
            cell.border = THIN_BORDER
            cell.alignment = CENTER_ALIGN


def build_full_report(data_date, data_dir, output_file):
    """
    整合多数据源生成完整Excel报告
    Sheet1: 品牌汇总（核心指标）
    Sheet2: 门店经营指标（首页门店明细表格）
    Sheet3: 商品销售排行（首页商品明细表格）
    Sheet4: 订单小时趋势
    Sheet5: 订单明细（API数据）
    Sheet6: 数据说明
    """
    wb = openpyxl.Workbook()

    # 加载数据
    orders = []
    store_metrics = []
    product_metrics = []

    # 加载订单数据
    order_file = Path(data_dir) / f"订单API_{data_date}.json"
    if order_file.exists():
        with open(order_file, "r", encoding="utf-8") as f:
            order_data = json.load(f)
            orders = order_data.get("orders", [])
        print(f"✅ 订单数据: {len(orders)}条")
    else:
        print(f"⚠️ 订单数据文件不存在: {order_file}")

    # 加载首页表格数据
    table_file = Path(data_dir) / f"首页表格_{data_date}.json"
    if table_file.exists():
        with open(table_file, "r", encoding="utf-8") as f:
            table_data = json.load(f)
            if "store_metrics" in table_data:
                store_metrics = table_data["store_metrics"]
            if "product_metrics" in table_data:
                product_metrics = table_data["product_metrics"]
        print(f"✅ 门店经营指标: {len(store_metrics.get('rows', []))}条")
        print(f"✅ 商品销售明细: {len(product_metrics.get('rows', []))}条")
    else:
        print(f"⚠️ 首页表格数据文件不存在: {table_file}")

    # ============================================================
    # Sheet1: 品牌汇总
    # ============================================================
    ws1 = wb.active
    ws1.title = "品牌汇总"

    ws1["A1"] = f"F.LUX 牵牛花巡店数据报告 · {data_date}"
    ws1["A1"].font = TITLE_FONT
    ws1.merge_cells("A1:F1")

    ws1["A2"] = "品牌范围：F.LUX/F·LUX 全部门店（已剔除跨品牌门店）"
    ws1["A2"].font = Font(italic=True, color="666666")
    ws1.merge_cells("A2:F2")

    ws1["A3"] = f"数据生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    ws1["A3"].font = Font(italic=True, color="666666")
    ws1.merge_cells("A3:F3")

    ws1["A5"] = "核心指标"
    ws1["A5"].font = SUBTITLE_FONT

    # 计算订单指标
    total_orders = len(orders)
    completed_orders = len([o for o in orders if o.get("orderStatusDesc") == "已完成"])
    cancelled_orders = len([o for o in orders if "取消" in o.get("orderStatusDesc", "")])
    total_amount = sum(float(o.get("actualPayAmt", 0) or 0) / 100 for o in orders)
    active_stores = len(set(o.get("storeName", "") for o in orders if o.get("storeName")))

    # 门店指标
    store_count = len(store_metrics.get("rows", []))
    total_store_amount = 0
    for row in store_metrics.get("rows", []):
        try:
            total_store_amount += float(row[2].replace(",", ""))
        except (ValueError, IndexError):
            pass

    metrics = [
        ("有效订单数", total_orders, "单"),
        ("已完成订单", completed_orders, "单"),
        ("已取消订单", cancelled_orders, "单"),
        ("订单总金额(实付)", round(total_amount, 2), "元"),
        ("活跃门店数", active_stores, "家"),
        ("门店经营指标覆盖", store_count, "家"),
        ("门店总销售额", round(total_store_amount, 2), "元"),
    ]

    headers = ["指标", "数值", "单位"]
    for col, h in enumerate(headers, 1):
        ws1.cell(row=6, column=col, value=h)
    style_header(ws1, 6, 3)

    for i, (name, value, unit) in enumerate(metrics):
        row = 7 + i
        ws1.cell(row=row, column=1, value=name)
        ws1.cell(row=row, column=2, value=value)
        ws1.cell(row=row, column=3, value=unit)
    style_data(ws1, 7, 6 + len(metrics), 3)

    ws1.column_dimensions["A"].width = 22
    ws1.column_dimensions["B"].width = 15
    ws1.column_dimensions["C"].width = 10

    # ============================================================
    # Sheet2: 门店经营指标
    # ============================================================
    ws2 = wb.create_sheet("门店经营指标")

    ws2["A1"] = "门店经营指标（按有效订单金额排序）"
    ws2["A1"].font = SUBTITLE_FONT
    ws2.merge_cells("A1:L1")

    if store_metrics and store_metrics.get("headers"):
        headers = store_metrics["headers"]
        for col, h in enumerate(headers, 1):
            ws2.cell(row=3, column=col, value=h)
        style_header(ws2, 3, len(headers))

        for i, row in enumerate(store_metrics.get("rows", []), 1):
            for col, val in enumerate(row, 1):
                ws2.cell(row=3 + i, column=col, value=val)

        style_data(ws2, 4, 3 + len(store_metrics["rows"]), len(headers))

        # 列宽
        ws2.column_dimensions["A"].width = 8
        ws2.column_dimensions["B"].width = 40
        for col in range(3, len(headers) + 1):
            ws2.column_dimensions[chr(64 + col)].width = 14
    else:
        ws2["A3"] = "暂无门店经营指标数据"

    # ============================================================
    # Sheet3: 商品销售排行
    # ============================================================
    ws3 = wb.create_sheet("商品销售排行")

    ws3["A1"] = "商品销售排行 TOP50"
    ws3["A1"].font = SUBTITLE_FONT
    ws3.merge_cells("A1:E1")

    if product_metrics and product_metrics.get("headers"):
        headers = product_metrics["headers"]
        for col, h in enumerate(headers, 1):
            ws3.cell(row=3, column=col, value=h)
        style_header(ws3, 3, len(headers))

        for i, row in enumerate(product_metrics.get("rows", []), 1):
            for col, val in enumerate(row, 1):
                ws3.cell(row=3 + i, column=col, value=val)

        style_data(ws3, 4, 3 + len(product_metrics["rows"]), len(headers))

        ws3.column_dimensions["A"].width = 8
        ws3.column_dimensions["B"].width = 60
        for col in range(3, len(headers) + 1):
            ws3.column_dimensions[chr(64 + col)].width = 16
    else:
        ws3["A3"] = "暂无商品销售数据"

    # ============================================================
    # Sheet4: 订单小时趋势
    # ============================================================
    ws4 = wb.create_sheet("订单小时趋势")

    ws4["A1"] = "24小时订单趋势"
    ws4["A1"].font = SUBTITLE_FONT
    ws4.merge_cells("A1:D1")

    headers = ["小时", "订单数", "订单金额(元)", "占比"]
    for col, h in enumerate(headers, 1):
        ws4.cell(row=3, column=col, value=h)
    style_header(ws4, 3, 4)

    hour_stats = defaultdict(lambda: {"count": 0, "amount": 0.0})
    for o in orders:
        create_time = o.get("createTime")
        if create_time:
            try:
                hour = (datetime.fromtimestamp(int(create_time) / 1000) + timedelta(hours=8)).hour
                hour_stats[hour]["count"] += 1
                price = o.get("actualPayAmt") or 0
                hour_stats[hour]["amount"] += float(price) / 100
            except (ValueError, TypeError):
                pass

    for hour in range(24):
        row = 4 + hour
        stats = hour_stats.get(hour, {"count": 0, "amount": 0.0})
        percentage = round(stats["count"] / total_orders * 100, 1) if total_orders > 0 else 0
        ws4.cell(row=row, column=1, value=f"{hour:02d}:00")
        ws4.cell(row=row, column=2, value=stats["count"])
        ws4.cell(row=row, column=3, value=round(stats["amount"], 2))
        ws4.cell(row=row, column=4, value=f"{percentage}%")

    style_data(ws4, 4, 27, 4)
    ws4.column_dimensions["A"].width = 12
    ws4.column_dimensions["B"].width = 12
    ws4.column_dimensions["C"].width = 15
    ws4.column_dimensions["D"].width = 10

    # ============================================================
    # Sheet5: 订单明细
    # ============================================================
    ws5 = wb.create_sheet("订单明细")

    ws5["A1"] = f"订单明细（共{len(orders)}条）"
    ws5["A1"].font = SUBTITLE_FONT
    ws5.merge_cells("A1:H1")

    headers = ["订单号", "门店名称", "下单时间", "订单状态", "实付金额(元)", "商品数", "商品明细", "渠道"]
    for col, h in enumerate(headers, 1):
        ws5.cell(row=3, column=col, value=h)
    style_header(ws5, 3, 8)

    for i, o in enumerate(orders, 1):
        row = 3 + i
        order_id = o.get("orderId", "")
        store = o.get("storeName", "")
        create_time = o.get("createTime")
        if create_time:
            try:
                time_str = (datetime.fromtimestamp(int(create_time) / 1000) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                time_str = str(create_time)
        else:
            time_str = ""
        status = o.get("orderStatusDesc", "")
        price = o.get("actualPayAmt") or 0
        products = o.get("productList", [])
        product_summary = "; ".join([f"{p.get('skuName','')[:20]}x{p.get('count',0)}" for p in products[:3]])
        if len(products) > 3:
            product_summary += f" 等{len(products)}件"
        channel = o.get("channelName", "")

        ws5.cell(row=row, column=1, value=str(order_id))
        ws5.cell(row=row, column=2, value=store)
        ws5.cell(row=row, column=3, value=time_str)
        ws5.cell(row=row, column=4, value=status)
        ws5.cell(row=row, column=5, value=round(float(price) / 100, 2) if price else 0)
        ws5.cell(row=row, column=6, value=len(products))
        ws5.cell(row=row, column=7, value=product_summary)
        ws5.cell(row=row, column=8, value=channel)

        if "取消" in status:
            for col in range(1, 9):
                ws5.cell(row=row, column=col).fill = WARN_FILL

    style_data(ws5, 4, 3 + len(orders), 8)
    ws5.column_dimensions["A"].width = 22
    ws5.column_dimensions["B"].width = 35
    ws5.column_dimensions["C"].width = 20
    ws5.column_dimensions["D"].width = 12
    ws5.column_dimensions["E"].width = 13
    ws5.column_dimensions["F"].width = 8
    ws5.column_dimensions["G"].width = 50
    ws5.column_dimensions["H"].width = 12

    # ============================================================
    # Sheet6: 数据说明
    # ============================================================
    ws6 = wb.create_sheet("数据说明")

    ws6["A1"] = "数据说明与采集状态"
    ws6["A1"].font = SUBTITLE_FONT

    notes = [
        ("数据日期", data_date),
        ("生成时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        ("", ""),
        ("已实现数据路径", ""),
        ("路径1: 订单API", f"✅ {len(orders)}条订单明细（按小时切片采集）"),
        ("路径2: 门店经营指标", f"✅ {len(store_metrics.get('rows', []))}家门店（首页表格提取）"),
        ("路径4: 商品销售明细", f"✅ {len(product_metrics.get('rows', []))}个商品（首页表格提取）"),
        ("", ""),
        ("待调试数据路径", ""),
        ("路径3: 门店流量数据", "⏳ 待调试（流量概览页面）"),
        ("路径5: 品类分析数据", "⏳ 待调试（品类分析页面）"),
        ("路径6: 门店在售商品", "⏳ 待调试（门店商品页面）"),
        ("", ""),
        ("数据来源说明", ""),
        ("订单数据", "通过牵牛花订单API /api/v1/orderfuse/newQueryList 采集"),
        ("门店/商品指标", "通过牵牛花首页表格DOM直接提取，支持分页"),
        ("金额单位", "订单API返回单位为分，已转换为元"),
        ("时间时区", "API返回UTC时间戳，已转换为北京时间(+8)"),
    ]

    for i, (key, value) in enumerate(notes, 3):
        ws6.cell(row=i, column=1, value=key)
        ws6.cell(row=i, column=2, value=value)
        if key and not value:
            ws6.cell(row=i, column=1).font = Font(bold=True, color="4472C4")

    ws6.column_dimensions["A"].width = 25
    ws6.column_dimensions["B"].width = 60

    # 保存
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_file)
    print(f"\n✅ 完整Excel报告已生成: {output_file}")
    print(f"   Sheet数: {len(wb.sheetnames)}")
    print(f"   Sheet列表: {', '.join(wb.sheetnames)}")
    return output_file


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if len(sys.argv) < 3:
        print("用法: python3 build_full_report.py <数据日期> <数据目录> <输出Excel>")
        print("示例: python3 build_full_report.py 2026-09-17 data/raw/2026-09-17 output/巡店报告_2026-09-17.xlsx")
        sys.exit(1)

    data_date = sys.argv[1]
    data_dir = sys.argv[2]
    output_file = sys.argv[3]

    build_full_report(data_date, data_dir, output_file)
