#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
表2：配送履约数据下载
数据→配送分析→配送概览
包含两个子表：
1. 配送距离分布表
2. 配送指标明细表（默认全选）
颗粒度：各门店维度
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


def switch_to_daily(qn):
    """切换统计周期为按日"""
    expr = '''
    (() => {
        const all = document.querySelectorAll('*');
        for (const el of all) {
            if (el.children.length === 0 && el.textContent.trim() === '按日') {
                const rect = el.getBoundingClientRect();
                if (rect.width > 0 && rect.y < 200) {
                    el.click();
                    return 'clicked';
                }
            }
        }
        return 'not found';
    })()
    '''
    result = qn.js(expr)
    time.sleep(2)
    print(f"  切换按日: {result}")
    return result == 'clicked'


def export_delivery_distance(qn):
    """导出配送距离分布表"""
    # 切换到按配送距离Tab
    expr_tab = '''
    (() => {
        const all = document.querySelectorAll('*');
        for (const el of all) {
            if (el.children.length === 0 && el.textContent.trim() === '按配送距离') {
                const rect = el.getBoundingClientRect();
                if (rect.width > 0) {
                    el.click();
                    return 'clicked';
                }
            }
        }
        return 'not found';
    })()
    '''
    qn.js(expr_tab)
    time.sleep(2)
    
    # 滚动到导出按钮
    qn.js("document.querySelector('.emp-flower-pc-data-export-btn')?.scrollIntoView({block: 'center'})", await_promise=False)
    time.sleep(1)
    
    # 点击第一个导出按钮（配送距离分布）
    expr_export = '''
    (() => {
        const btns = document.querySelectorAll('.emp-flower-pc-data-export-btn');
        if (btns.length > 0) {
            btns[0].click();
            return 'clicked';
        }
        return 'no_btn';
    })()
    '''
    result = qn.js(expr_export)
    print(f"  配送距离分布导出: {result}")
    time.sleep(3)
    
    # 关闭弹窗
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
    
    # 等待任务完成
    task_id = None
    poll_start = time.time()
    while time.time() - poll_start < 120:
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
        for t in tasks:
            name = str(t.get("name", ""))
            if "配送距离" in name:
                state = str(t.get("state", ""))
                if "已完成" in state or "成功" in state:
                    print(f"  配送距离分布导出完成: {name} (id={t['id']})")
                    task_id = t["id"]
                    break
        if task_id:
            break
        time.sleep(3)
    
    if not task_id:
        raise RuntimeError("配送距离分布导出任务超时")
    
    return task_id


def export_delivery_metrics(qn):
    """导出配送指标明细表"""
    # 滚动到指标明细导出区域
    qn.js("document.querySelectorAll('.emp-flower-pc-data-export-btn')[1]?.scrollIntoView({block: 'center'})", await_promise=False)
    time.sleep(1)
    
    # 点击第二个导出按钮（指标明细）
    expr_export = '''
    (() => {
        const btns = document.querySelectorAll('.emp-flower-pc-data-export-btn');
        if (btns.length >= 2) {
            btns[1].click();
            return 'clicked';
        }
        return 'no_btn';
    })()
    '''
    result = qn.js(expr_export)
    print(f"  配送指标明细导出: {result}")
    time.sleep(3)
    
    # 关闭弹窗
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
    
    # 等待任务完成
    task_id = None
    poll_start = time.time()
    while time.time() - poll_start < 120:
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
        for t in tasks:
            name = str(t.get("name", ""))
            if "配送分析概览" in name:
                state = str(t.get("state", ""))
                if "已完成" in state or "成功" in state:
                    print(f"  配送指标明细导出完成: {name} (id={t['id']})")
                    task_id = t["id"]
                    break
        if task_id:
            break
        time.sleep(3)
    
    if not task_id:
        raise RuntimeError("配送指标明细导出任务超时")
    
    return task_id


def main():
    parser = argparse.ArgumentParser(description="牵牛花配送履约数据下载")
    parser.add_argument("--date", help="数据日期 YYYY-MM-DD，默认昨日")
    parser.add_argument("--out", help="输出目录")
    args = parser.parse_args()

    date_str = args.date or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    out_dir = Path(args.out) if args.out else get_raw_dir(date_str)

    print(f"{'='*60}")
    print(f"表2：配送履约数据下载")
    print(f"日期: {date_str}")
    print(f"{'='*60}\n")

    t0 = time.time()
    with QianniuCDP() as qn:
        print(f"已连接: {qn.page_url}")
        
        # 导航到配送分析页面
        qn.goto("#/data/delivery/overview/new", settle=10)
        print(f"已导航到配送分析页面")
        
        # 切换按日
        switch_to_daily(qn)
        time.sleep(3)
        
        # 导出配送距离分布
        print(f"\n--- 导出配送距离分布表 ---")
        task_id1 = export_delivery_distance(qn)
        output_file1 = out_dir / f"配送距离分布_{date_str}.xlsx"
        qn.download_task_file(task_id1, str(output_file1))
        file_size1 = output_file1.stat().st_size if output_file1.exists() else 0
        print(f"  ✅ 配送距离分布: {output_file1.name} ({file_size1/1024:.1f} KB)")
        
        # 导出配送指标明细
        print(f"\n--- 导出配送指标明细表 ---")
        task_id2 = export_delivery_metrics(qn)
        output_file2 = out_dir / f"配送指标明细_{date_str}.xlsx"
        qn.download_task_file(task_id2, str(output_file2))
        file_size2 = output_file2.stat().st_size if output_file2.exists() else 0
        print(f"  ✅ 配送指标明细: {output_file2.name} ({file_size2/1024:.1f} KB)")

    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"全部完成，耗时: {elapsed:.1f}秒")
    print(f"输出目录: {out_dir}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
