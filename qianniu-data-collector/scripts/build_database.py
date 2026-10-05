#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
F.LUX品牌数据数据库构建脚本
创建SQLite数据库，导入闪购端历史数据
"""

import sqlite3
import pandas as pd
from pathlib import Path
import argparse


DB_PATH = Path(__file__).parent.parent / "data" / "db" / "flux_data.db"


def init_db(conn):
    """初始化数据库表结构"""
    cursor = conn.cursor()
    
    # 门店映射表（主数据）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS store_mapping (
            store_id TEXT PRIMARY KEY,
            store_name_std TEXT NOT NULL,
            store_name_shangou TEXT,
            store_name_qianniu TEXT,
            city TEXT,
            province TEXT,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 门店日表（整合层）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS store_daily (
            date TEXT NOT NULL,
            store_id TEXT NOT NULL,
            -- 经营指标
            order_count INTEGER,
            gmv REAL,
            avg_order_value REAL,
            product_original_sales REAL,
            package_fee REAL,
            delivery_fee REAL,
            customer_count INTEGER,
            overtime_rate REAL,
            -- 流量指标
            exposure_count INTEGER,
            visit_count INTEGER,
            add_cart_count INTEGER,
            order_user_count INTEGER,
            visit_rate REAL,
            -- 商品指标
            product_sales_rate REAL,
            total_products INTEGER,
            stockout_count INTEGER,
            absence_count INTEGER,
            absence_loss REAL,
            -- 配送指标
            delivery_order_count INTEGER,
            delivery_overtime_count INTEGER,
            avg_delivery_distance REAL,
            avg_delivery_cost REAL,
            -- 营业指标
            open_hours REAL,
            absence_hours REAL,
            open_status TEXT,
            -- 财务指标
            revenue REAL,
            cost REAL,
            gross_profit REAL,
            net_profit REAL,
            -- 元数据
            source TEXT DEFAULT 'shangou',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (date, store_id)
        )
    ''')
    
    # 商品日表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS product_daily (
            date TEXT NOT NULL,
            store_id TEXT NOT NULL,
            product_id TEXT NOT NULL,
            product_name TEXT,
            category TEXT,
            sales_count INTEGER,
            sales_amount REAL,
            actual_pay_amount REAL,
            refund_amount REAL,
            exposure_count INTEGER,
            visit_count INTEGER,
            add_cart_count INTEGER,
            order_count INTEGER,
            conversion_rate REAL,
            stock_status TEXT,
            source TEXT DEFAULT 'shangou',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (date, store_id, product_id)
        )
    ''')
    
    # 订单明细表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS order_detail (
            order_id TEXT NOT NULL,
            order_time TEXT,
            store_id TEXT,
            product_id TEXT,
            product_name TEXT,
            amount REAL,
            actual_pay_amount REAL,
            refund_amount REAL,
            order_status TEXT,
            delivery_status TEXT,
            refund_reason TEXT,
            after_sale_type TEXT,
            source TEXT DEFAULT 'shangou',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (order_id, product_id)
        )
    ''')
    
    # 评价表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS review (
            review_id TEXT PRIMARY KEY,
            order_id TEXT,
            store_id TEXT,
            review_time TEXT,
            rating REAL,
            content TEXT,
            append_content TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 创建索引
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_store_daily_store ON store_daily(store_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_store_daily_date ON store_daily(date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_product_daily_store ON product_daily(store_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_product_daily_date ON product_daily(date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_product_daily_product ON product_daily(product_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_order_store ON order_detail(store_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_order_time ON order_detail(order_time)')
    
    conn.commit()
    print("数据库表结构初始化完成")


def import_shangou_data(conn, excel_path):
    """导入闪购端Excel数据"""
    print(f"\n导入闪购端数据: {excel_path}")
    
    xls = pd.ExcelFile(excel_path)
    
    # 1. 门店财务明细 -> store_daily
    if "门店财务明细" in xls.sheet_names:
        print("  导入门店财务明细...")
        df = pd.read_excel(xls, "门店财务明细")
        # 标准化列名和数据
        df.columns = [str(c).strip() for c in df.columns]
        print(f"    行数: {len(df)}")
    
    # 2. 门店成交明细 -> store_daily
    if "门店成交明细" in xls.sheet_names:
        print("  导入门店成交明细...")
        df = pd.read_excel(xls, "门店成交明细")
        df.columns = [str(c).strip() for c in df.columns]
        print(f"    行数: {len(df)}")
    
    # 3. 流量明细 -> store_daily
    if "流量明细(新)" in xls.sheet_names:
        print("  导入流量明细...")
        df = pd.read_excel(xls, "流量明细(新)")
        df.columns = [str(c).strip() for c in df.columns]
        print(f"    行数: {len(df)}")
    
    # 4. 商品数据 -> order_detail
    if "商品数据" in xls.sheet_names:
        print("  导入商品数据...")
        df = pd.read_excel(xls, "商品数据")
        df.columns = [str(c).strip() for c in df.columns]
        print(f"    行数: {len(df)}")
    
    # 5. 问题订单 -> order_detail
    if "问题订单数据" in xls.sheet_names:
        print("  导入问题订单数据...")
        df = pd.read_excel(xls, "问题订单数据")
        df.columns = [str(c).strip() for c in df.columns]
        print(f"    行数: {len(df)}")
    
    # 6. 评价数据 -> review
    if "评价数据" in xls.sheet_names:
        print("  导入评价数据...")
        df = pd.read_excel(xls, "评价数据")
        df.columns = [str(c).strip() for c in df.columns]
        print(f"    行数: {len(df)}")
    
    # 7. 售后订单 -> order_detail
    if "售后订单数据" in xls.sheet_names:
        print("  导入售后订单数据...")
        df = pd.read_excel(xls, "售后订单数据")
        df.columns = [str(c).strip() for c in df.columns]
        print(f"    行数: {len(df)}")
    
    print("  数据导入完成（占位，后续完善字段映射）")


def main():
    parser = argparse.ArgumentParser(description="构建F.LUX品牌数据数据库")
    parser.add_argument("--shangou-file", help="闪购端汇总表路径")
    args = parser.parse_args()
    
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(DB_PATH)
    print(f"数据库: {DB_PATH}")
    
    # 初始化表结构
    init_db(conn)
    
    # 导入闪购数据
    if args.shangou_file:
        import_shangou_data(conn, args.shangou_file)
    
    # 查看表大小
    cursor = conn.cursor()
    print("\n数据库表统计:")
    for table in ["store_mapping", "store_daily", "product_daily", "order_detail", "review"]:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
        print(f"  {table}: {count} 行")
    
    conn.close()
    print("\n数据库构建完成！")


if __name__ == "__main__":
    main()
