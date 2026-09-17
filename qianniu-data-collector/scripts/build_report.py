#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
牵牛花数据采集 - 数据整合与Excel报告生成
将采集到的原始数据整合为多Sheet的Excel成品文件
"""

import json
import logging
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime, timedelta

try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
except ImportError:
    print("需要安装 openpyxl: pip install openpyxl")
    raise


def build_excel_report(data_date, input_file, output_file):
    """
    从订单JSON生成多Sheet Excel报告
    Sheet1: 品牌汇总
    Sheet2: 门店明细
    Sheet3: 小时趋势
    Sheet4: 商品销量排行
    Sheet5: 订单明细
    """
    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    orders = data.get("orders", [])
    print(f"读取订单数: {len(orders)}")

    # 创建工作簿
    wb = openpyxl.Workbook()

    # 样式定义
    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    title_font = Font(bold=True, size=14)
    subtitle_font = Font(bold=True, size=11, color="4472C4")
    center_align = Alignment(horizontal="center", vertical="center")
    left_align = Alignment(horizontal="left", vertical="center")
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin')
    )
    warn_fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
    good_fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")

    def style_header(ws, row, cols):
        for col in range(1, cols + 1):
            cell = ws.cell(row=row, column=col)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = center_align
            cell.border = thin_border

    def style_data(ws, start_row, end_row, cols):
        for row in range(start_row, end_row + 1):
            for col in range(1, cols + 1):
                cell = ws.cell(row=row, column=col)
                cell.border = thin_border
                cell.alignment = center_align

    # ============================================================
    # Sheet1: 品牌汇总
    # ============================================================
    ws1 = wb.active
    ws1.title = "品牌汇总"

    ws1["A1"] = f"F.LUX 牵牛花巡店数据报告 · {data_date}"
    ws1["A1"].font = title_font
    ws1.merge_cells("A1:F1")

    ws1["A2"] = f"品牌范围：46家 F.LUX/F·LUX 门店（已剔除跨品牌门店：觅初）"
    ws1["A2"].font = Font(italic=True, color="666666")
    ws1.merge_cells("A2:F2")

    ws1["A3"] = f"数据生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    ws1["A3"].font = Font(italic=True, color="666666")
    ws1.merge_cells("A3:F3")

    # 核心指标
    ws1["A5"] = "核心指标"
    ws1["A5"].font = subtitle_font

    total_orders = len(orders)
    completed_orders = len([o for o in orders if o.get("orderStatusDesc") == "已完成"])
    cancelled_orders = len([o for o in orders if "取消" in o.get("orderStatusDesc", "")])

    # 计算总销售额（actualPayAmt 单位是分，除以100）
    total_amount = 0
    for o in orders:
        price = o.get("actualPayAmt") or o.get("bizReceiveAmt") or 0
        try:
            total_amount += float(price) / 100
        except (ValueError, TypeError):
            pass

    # 门店数
    stores = set(o.get("storeName", "") for o in orders if o.get("storeName"))
    active_stores = len(stores)

    # 商品数（按skuId去重）
    all_products = set()
    for o in orders:
        for p in o.get("productList", []):
            sku = p.get("skuId") or p.get("spuId") or p.get("skuName")
            if sku:
                all_products.add(sku)

    metrics = [
        ("有效订单数", total_orders, "单"),
        ("已完成订单", completed_orders, "单"),
        ("已取消订单", cancelled_orders, "单"),
        ("订单总金额", round(total_amount, 2), "元"),
        ("活跃门店数", active_stores, "家"),
        ("动销商品数", len(all_products), "SPU"),
    ]

    ws1.append([])  # row4
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

    # 列宽
    ws1.column_dimensions["A"].width = 20
    ws1.column_dimensions["B"].width = 15
    ws1.column_dimensions["C"].width = 10

    # ============================================================
    # Sheet2: 门店明细
    # ============================================================
    ws2 = wb.create_sheet("门店明细")

    ws2["A1"] = "门店订单明细"
    ws2["A1"].font = subtitle_font
    ws2.merge_cells("A1:G1")

    headers = ["排名", "门店名称", "订单数", "已完成", "已取消", "订单金额(元)", "完成率"]
    for col, h in enumerate(headers, 1):
        ws2.cell(row=3, column=col, value=h)
    style_header(ws2, 3, 7)

    # 按门店统计
    store_stats = defaultdict(lambda: {"total": 0, "completed": 0, "cancelled": 0, "amount": 0.0})
    for o in orders:
        store = o.get("storeName", "未知")
        store_stats[store]["total"] += 1
        status = o.get("orderStatusDesc", "")
        if status == "已完成":
            store_stats[store]["completed"] += 1
        if "取消" in status:
            store_stats[store]["cancelled"] += 1
        price = o.get("actualPayAmt") or o.get("bizReceiveAmt") or 0
        try:
            store_stats[store]["amount"] += float(price) / 100
        except (ValueError, TypeError):
            pass

    # 按订单数排序
    sorted_stores = sorted(store_stats.items(), key=lambda x: x[1]["total"], reverse=True)

    for i, (store, stats) in enumerate(sorted_stores, 1):
        row = 3 + i
        completion_rate = round(stats["completed"] / stats["total"] * 100, 1) if stats["total"] > 0 else 0
        ws2.cell(row=row, column=1, value=i)
        ws2.cell(row=row, column=2, value=store)
        ws2.cell(row=row, column=3, value=stats["total"])
        ws2.cell(row=row, column=4, value=stats["completed"])
        ws2.cell(row=row, column=5, value=stats["cancelled"])
        ws2.cell(row=row, column=6, value=round(stats["amount"], 2))
        ws2.cell(row=row, column=7, value=f"{completion_rate}%")

        # 低完成率标红
        if completion_rate < 80:
            ws2.cell(row=row, column=7).fill = warn_fill

    style_data(ws2, 4, 3 + len(sorted_stores), 7)

    ws2.column_dimensions["A"].width = 8
    ws2.column_dimensions["B"].width = 40
    ws2.column_dimensions["C"].width = 10
    ws2.column_dimensions["D"].width = 10
    ws2.column_dimensions["E"].width = 10
    ws2.column_dimensions["F"].width = 15
    ws2.column_dimensions["G"].width = 10

    # ============================================================
    # Sheet3: 小时趋势
    # ============================================================
    ws3 = wb.create_sheet("小时趋势")

    ws3["A1"] = "24小时订单趋势"
    ws3["A1"].font = subtitle_font
    ws3.merge_cells("A1:D1")

    headers = ["小时", "订单数", "订单金额(元)", "占比"]
    for col, h in enumerate(headers, 1):
        ws3.cell(row=3, column=col, value=h)
    style_header(ws3, 3, 4)

    # 按小时统计（时间戳是UTC，加8小时转北京时间）
    hour_stats = defaultdict(lambda: {"count": 0, "amount": 0.0})
    for o in orders:
        create_time = o.get("createTime")
        if create_time:
            try:
                # UTC时间戳 + 8小时 = 北京时间
                hour = (datetime.fromtimestamp(int(create_time) / 1000) + timedelta(hours=8)).hour
                hour_stats[hour]["count"] += 1
                price = o.get("actualPayAmt") or o.get("bizReceiveAmt") or 0
                hour_stats[hour]["amount"] += float(price) / 100
            except (ValueError, TypeError):
                pass

    for hour in range(24):
        row = 4 + hour
        stats = hour_stats.get(hour, {"count": 0, "amount": 0.0})
        percentage = round(stats["count"] / total_orders * 100, 1) if total_orders > 0 else 0
        ws3.cell(row=row, column=1, value=f"{hour:02d}:00")
        ws3.cell(row=row, column=2, value=stats["count"])
        ws3.cell(row=row, column=3, value=round(stats["amount"], 2))
        ws3.cell(row=row, column=4, value=f"{percentage}%")

    style_data(ws3, 4, 27, 4)

    ws3.column_dimensions["A"].width = 12
    ws3.column_dimensions["B"].width = 12
    ws3.column_dimensions["C"].width = 15
    ws3.column_dimensions["D"].width = 10

    # ============================================================
    # Sheet4: 商品销量排行
    # ============================================================
    ws4 = wb.create_sheet("商品销量排行")

    ws4["A1"] = "商品销量排行 TOP50"
    ws4["A1"].font = subtitle_font
    ws4.merge_cells("A1:E1")

    headers = ["排名", "SPU名称", "销量", "销售额(元)", "下单门店数"]
    for col, h in enumerate(headers, 1):
        ws4.cell(row=3, column=col, value=h)
    style_header(ws4, 3, 5)

    # 按商品统计
    product_stats = defaultdict(lambda: {"quantity": 0, "amount": 0.0, "stores": set()})
    for o in orders:
        store = o.get("storeName", "")
        for p in o.get("productList", []):
            name = p.get("skuName") or p.get("spuName") or "未知"
            qty = p.get("count") or p.get("quantity") or 0
            price = p.get("totalPayAmount") or p.get("unitPrice") or 0
            try:
                product_stats[name]["quantity"] += int(qty)
                product_stats[name]["amount"] += float(price) / 100
                product_stats[name]["stores"].add(store)
            except (ValueError, TypeError):
                pass

    sorted_products = sorted(product_stats.items(), key=lambda x: x[1]["quantity"], reverse=True)[:50]

    for i, (name, stats) in enumerate(sorted_products, 1):
        row = 3 + i
        ws4.cell(row=row, column=1, value=i)
        ws4.cell(row=row, column=2, value=name)
        ws4.cell(row=row, column=3, value=stats["quantity"])
        ws4.cell(row=row, column=4, value=round(stats["amount"], 2))
        ws4.cell(row=row, column=5, value=len(stats["stores"]))

    style_data(ws4, 4, 3 + len(sorted_products), 5)

    ws4.column_dimensions["A"].width = 8
    ws4.column_dimensions["B"].width = 50
    ws4.column_dimensions["C"].width = 10
    ws4.column_dimensions["D"].width = 15
    ws4.column_dimensions["E"].width = 12

    # ============================================================
    # Sheet5: 订单明细
    # ============================================================
    ws5 = wb.create_sheet("订单明细")

    ws5["A1"] = "订单明细（全部）"
    ws5["A1"].font = subtitle_font
    ws5.merge_cells("A1:H1")

    headers = ["订单号", "门店名称", "下单时间", "订单状态", "订单金额(元)", "商品数", "商品明细", "联系方式"]
    for col, h in enumerate(headers, 1):
        ws5.cell(row=3, column=col, value=h)
    style_header(ws5, 3, 8)

    for i, o in enumerate(orders, 1):
        row = 3 + i
        order_id = o.get("orderId") or o.get("id") or ""
        store = o.get("storeName", "")
        create_time = o.get("createTime")
        if create_time:
            try:
                # UTC时间戳 + 8小时 = 北京时间
                time_str = (datetime.fromtimestamp(int(create_time) / 1000) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                time_str = str(create_time)
        else:
            time_str = ""
        status = o.get("orderStatusDesc", "")
        price = o.get("actualPayAmt") or o.get("bizReceiveAmt") or 0
        products = o.get("productList", [])
        product_summary = "; ".join([f"{p.get('skuName','')[:20]}x{p.get('count',0)}" for p in products[:3]])
        if len(products) > 3:
            product_summary += f" 等{len(products)}件"
        phone = o.get("userPrivacyPhone") or o.get("receiverPhone") or ""

        ws5.cell(row=row, column=1, value=str(order_id))
        ws5.cell(row=row, column=2, value=store)
        ws5.cell(row=row, column=3, value=time_str)
        ws5.cell(row=row, column=4, value=status)
        ws5.cell(row=row, column=5, value=round(float(price) / 100, 2) if price else 0)
        ws5.cell(row=row, column=6, value=len(products))
        ws5.cell(row=row, column=7, value=product_summary)
        ws5.cell(row=row, column=8, value=str(phone) if phone else "")

        if "取消" in status:
            for col in range(1, 9):
                ws5.cell(row=row, column=col).fill = warn_fill

    style_data(ws5, 4, 3 + len(orders), 8)

    ws5.column_dimensions["A"].width = 20
    ws5.column_dimensions["B"].width = 35
    ws5.column_dimensions["C"].width = 20
    ws5.column_dimensions["D"].width = 12
    ws5.column_dimensions["E"].width = 12
    ws5.column_dimensions["F"].width = 8
    ws5.column_dimensions["G"].width = 50
    ws5.column_dimensions["H"].width = 15

    # 保存
    wb.save(output_file)
    print(f"✅ Excel报告已生成: {output_file}")
    print(f"   Sheet数: {len(wb.sheetnames)}")
    print(f"   Sheet列表: {', '.join(wb.sheetnames)}")
    return output_file


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if len(sys.argv) < 3:
        print("用法: python3 build_report.py <数据日期> <输入JSON> <输出Excel>")
        print("示例: python3 build_report.py 2026-09-16 data/raw/2026-09-16/订单API_2026-09-16.json output/巡店报告_2026-09-16.xlsx")
        sys.exit(1)

    data_date = sys.argv[1]
    input_file = sys.argv[2]
    output_file = sys.argv[3]

    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    build_excel_report(data_date, input_file, output_file)
