#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
表1：门店商品指标明细下载
数据→商品详情→指标全选(36个)→维度选门店→查询→导出
用途：门店汇总维度最细的商品指标表，包含销售额/销量/订单/商品数/缺勤等36个指标
"""

import json
import sys
import time
import argparse
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent))
from qn_cdp import QianniuCDP
from common import get_raw_dir


def select_all_metrics(qn):
    """全选所有指标（5个分组）"""
    # 1. 点击指标select打开抽屉
    open_expr = '''
    (() => {
        const all = document.querySelectorAll('*');
        for (const el of all) {
            if (el.children.length === 0) {
                const text = el.textContent.trim();
                const rect = el.getBoundingClientRect();
                if (rect.width > 0 && text.match(/^\\+\\s*\\d+\\s*\\.\\.\\.$/) && rect.y > 400 && rect.y < 500) {
                    el.parentElement.click();
                    return 'opened';
                }
            }
        }
        return 'not_found';
    })()
    '''
    result = qn.js(open_expr)
    time.sleep(3)
    print(f"  打开指标选择: {result}")
    
    if result != 'opened':
        return False
    
    # 2. 点击所有5个分组的全选复选框
    select_all_expr = '''
    (() => {
        const all = document.querySelectorAll('*');
        let count = 0;
        for (const el of all) {
            if (el.children.length === 0 && el.textContent.trim() === '全选') {
                const rect = el.getBoundingClientRect();
                if (rect.width > 0 && rect.x > 800) {
                    const parent = el.parentElement;
                    const checkbox = parent.querySelector('.data-ant-checkbox, [class*="checkbox"]');
                    if (checkbox) {
                        checkbox.click();
                        count++;
                    }
                }
            }
        }
        return `clicked ${count} checkboxes`;
    })()
    '''
    result2 = qn.js(select_all_expr)
    time.sleep(1)
    print(f"  全选指标: {result2}")
    
    # 3. 点击确定按钮
    confirm_expr = '''
    (() => {
        const all = document.querySelectorAll('button, span, div');
        for (const el of all) {
            if (el.children.length === 0) {
                const text = el.textContent.trim();
                const rect = el.getBoundingClientRect();
                if (rect.width > 0 && rect.x > 800 && (text === '确 定' || text === '确定')) {
                    el.click();
                    return 'confirmed';
                }
            }
        }
        return 'no_confirm';
    })()
    '''
    result3 = qn.js(confirm_expr)
    time.sleep(2)
    print(f"  确认选择: {result3}")
    
    return result3 == 'confirmed'


def switch_to_store_dimension(qn):
    """切换维度到门店"""
    expr = '''
    (() => {
        const all = document.querySelectorAll('*');
        let detailTitle = null;
        for (const el of all) {
            if (el.children.length === 0 && el.textContent.trim() === '指标明细导出') {
                const rect = el.getBoundingClientRect();
                if (rect.width > 0) {
                    detailTitle = rect;
                    break;
                }
            }
        }
        if (!detailTitle) return 'no detail title';
        
        for (const el of all) {
            if (el.children.length === 0 && el.textContent.trim() === '门店') {
                const rect = el.getBoundingClientRect();
                if (rect.width > 0 && rect.y > detailTitle.y && rect.y < detailTitle.y + 100) {
                    el.click();
                    return 'clicked';
                }
            }
        }
        return 'not_found';
    })()
    '''
    result = qn.js(expr)
    time.sleep(2)
    print(f"  切换门店维度: {result}")
    return result == 'clicked'


def query_data(qn):
    """点击查询按钮"""
    expr = '''
    (() => {
        const btns = document.querySelectorAll('.data-roo-btn, button');
        for (const btn of btns) {
            if (btn.textContent.trim() === '查 询' || btn.textContent.trim() === '查询') {
                btn.click();
                return 'clicked';
            }
        }
        return 'not_found';
    })()
    '''
    result = qn.js(expr)
    print(f"  查询: {result}")
    return result == 'clicked'


def click_export(qn):
    """点击导出按钮"""
    expr = '''
    (() => {
        const btn = document.querySelector('.emp-flower-pc-data-export-btn');
        if (btn) {
            btn.click();
            return 'clicked';
        }
        return 'no_btn';
    })()
    '''
    result = qn.js(expr)
    print(f"  导出按钮: {result}")
    time.sleep(3)
    
    # 关闭弹窗（如果有完成按钮）
    close_expr = '''
    (() => {
        const btns = document.querySelectorAll('.data-ant-modal-confirm-btns button, .data-ant-btn');
        for (const btn of btns) {
            if (btn.textContent.trim() === '完 成' || btn.textContent.trim() === '完成') {
                btn.click();
                return 'closed';
            }
        }
        return 'no_modal';
    })()
    '''
    qn.js(close_expr)
    time.sleep(1)
    return result == 'clicked'


def poll_export_task(qn, timeout=180):
    """轮询任务中心，等待导出完成，返回taskId"""
    poll_start = time.time()
    while time.time() - poll_start < timeout:
        expr = r'''
        (async () => {
            const resp = await fetch('/api/v1/task/queryTasks', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({pageNo: 1, pageSize: 5})
            });
            const data = await resp.json();
            const list = (data.data && (data.data.list || data.data.tasks || data.data.records)) || [];
            return JSON.stringify(list.map(t => ({
                id: t.taskId,
                name: t.taskName,
                state: t.executingState
            })));
        })()
        '''
        raw = qn.js(expr, timeout=30)
        tasks = json.loads(raw) if raw else []
        
        # 找最新的商品详情导出任务
        for t in tasks:
            name = str(t.get("name", ""))
            if "商品详情" in name:
                state = str(t.get("state", ""))
                if "已完成" in state or "成功" in state:
                    print(f"  导出完成: {name} (id={t['id']})")
                    return t["id"]
                if "失败" in state:
                    raise RuntimeError(f"导出失败: {name} - {state}")
                print(f"  任务进行中: {state}")
                break
        
        time.sleep(5)
    
    raise TimeoutError("导出任务超时")


def export_store_product_metrics(qn, date_str):
    """导出门店商品指标明细数据"""
    # 导航到商品详情页面
    qn.goto("#/data/product/details", settle=8)
    print(f"  已导航到商品详情页面")
    
    # 全选所有指标（36个）
    select_all_metrics(qn)
    time.sleep(2)
    
    # 切换维度到门店
    switch_to_store_dimension(qn)
    time.sleep(2)
    
    # 查询数据
    query_data(qn)
    print("  等待数据加载...")
    time.sleep(10)
    
    # 点击导出
    click_export(qn)
    
    # 等待任务完成
    print("  等待导出任务...")
    task_id = poll_export_task(qn, timeout=120)
    
    return task_id


def main():
    parser = argparse.ArgumentParser(description="牵牛花门店商品指标明细下载")
    parser.add_argument("--date", help="数据日期 YYYY-MM-DD，默认昨日")
    parser.add_argument("--out", help="输出目录")
    args = parser.parse_args()

    date_str = args.date or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    out_dir = Path(args.out) if args.out else get_raw_dir(date_str)

    print(f"{'='*60}")
    print(f"表1：门店商品指标明细（商品详情-门店维度-36指标全选）")
    print(f"日期: {date_str}")
    print(f"{'='*60}\n")

    t0 = time.time()
    with QianniuCDP() as qn:
        print(f"已连接: {qn.page_url}")
        task_id = export_store_product_metrics(qn, date_str)
        
        output_file = out_dir / f"门店商品指标明细_{date_str}.xlsx"
        qn.download_task_file(task_id, str(output_file))
        file_size = output_file.stat().st_size if output_file.exists() else 0
        print(f"\n✅ 下载完成: {output_file.name} ({file_size/1024:.1f} KB)")

    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"完成，耗时: {elapsed:.1f}秒")
    print(f"输出目录: {out_dir}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
