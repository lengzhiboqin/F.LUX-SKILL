#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
表7：总部商品近30天流量排名下载
数据→流量→商品流量分析→统计周期选近30天→维度选按总部→查询→导出
用途：整理30天流量高的商品作为对标模板，为门店商品补充做参照
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


def set_period_30days(qn):
    """设置统计周期为近30天"""
    expr = '''
    (() => {
        const btns = document.querySelectorAll('.data-roo-btn');
        for (const btn of btns) {
            if (btn.textContent.trim() === '近30天') {
                btn.click();
                return 'clicked';
            }
        }
        return 'not_found';
    })()
    '''
    result = qn.js(expr)
    time.sleep(2)
    verify_expr = '''
    (() => {
        const btns = document.querySelectorAll('.data-roo-btn');
        for (const btn of btns) {
            if (btn.textContent.trim() === '近30天') {
                return btn.className.includes('active') || btn.className.includes('primary') ? 'selected' : 'not_selected';
            }
        }
        return 'not_found';
    })()
    '''
    state = qn.js(verify_expr)
    print(f"  近30天状态: {state}")
    return state == 'selected'


def set_dimension_hq(qn):
    """设置维度为按总部"""
    check_expr = '''
    (() => {
        const items = document.querySelectorAll('.data-ant-select-selection-item');
        for (const item of items) {
            if (item.textContent.trim() === '按总部') {
                return 'already_hq';
            }
        }
        return 'not_hq';
    })()
    '''
    state = qn.js(check_expr)
    if state == 'already_hq':
        print("  维度已是按总部")
        return True

    click_expr = '''
    (() => {
        const items = document.querySelectorAll('.data-ant-select-selection-item');
        for (const item of items) {
            if (item.textContent.trim() === '按门店' || item.textContent.trim() === '按总部') {
                const select = item.closest('.data-ant-select');
                if (select) {
                    select.click();
                    return 'opened';
                }
            }
        }
        return 'not_found';
    })()
    '''
    qn.js(click_expr)
    time.sleep(1)

    select_expr = '''
    (() => {
        const items = document.querySelectorAll('.data-ant-select-item-option-content');
        for (const item of items) {
            if (item.textContent.trim() === '按总部') {
                item.click();
                return 'selected_hq';
            }
        }
        return 'not_found';
    })()
    '''
    result = qn.js(select_expr)
    time.sleep(2)
    print(f"  维度切换结果: {result}")
    return 'hq' in str(result)


def click_query(qn):
    """点击查询按钮"""
    expr = '''
    (() => {
        const btns = document.querySelectorAll('.data-roo-btn');
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
    time.sleep(8)


def click_export(qn):
    """点击导出按钮"""
    expr = '''
    (() => {
        const btn = document.querySelector('.emp-flower-pc-data-export-btn');
        if (btn) {
            btn.click();
            return 'clicked';
        }
        return 'no_button';
    })()
    '''
    result = qn.js(expr)
    print(f"  导出: {result}")
    time.sleep(3)


def export_hq_30days(qn):
    """导出总部商品近30天流量排名"""
    qn.goto("#/data/product/flow-performance", settle=5)
    qn.js("window.scrollTo(0, 0)", await_promise=False)
    time.sleep(1)

    print("  设置统计周期: 近30天")
    set_period_30days(qn)

    print("  设置维度: 按总部")
    set_dimension_hq(qn)

    print("  点击查询，等待数据加载...")
    click_query(qn)

    print("  点击导出...")
    click_export(qn)

    try:
        close_expr = '''
        (() => {
            const btns = document.querySelectorAll('.data-ant-modal-confirm-btns button, .data-ant-modal-confirm-btns span');
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
    except Exception:
        pass

    print("  等待导出任务...")
    task_id = None
    poll_start = time.time()
    while time.time() - poll_start < 120:
        expr = r'''
        (async () => {
            const resp = await fetch('/api/v1/task/queryTasks', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({pageNo: 1, pageSize: 20})
            });
            const data = await resp.json();
            const list = (data.data && (data.data.list || data.data.tasks || data.data.records)) || [];
            return JSON.stringify(list.map(t => ({
                id: t.taskId || t.id,
                name: t.taskName || t.name,
                state: t.executingState
            })));
        })()
        '''
        raw = qn.js(expr, timeout=30)
        tasks = json.loads(raw) if raw else []
        for t in tasks:
            name = str(t.get("name", ""))
            if "商品流量" in name and "-" in name and "2026" in name:
                task_id = t["id"]
                print(f"  找到任务: {name} (id={task_id}, state={t.get('state')})")
                break
        if task_id:
            break
        time.sleep(3)

    if not task_id:
        raise RuntimeError("导出任务未生成")

    wait_start = time.time()
    while time.time() - wait_start < 180:
        expr = f'''
        (async () => {{
            const resp = await fetch('/api/v1/task/queryTasks', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{pageNo: 1, pageSize: 20}})
            }});
            const data = await resp.json();
            const list = (data.data && (data.data.list || data.data.tasks || data.data.records)) || [];
            const t = list.find(x => (x.taskId || x.id) == {task_id});
            return t ? JSON.stringify({{state: t.executingState}}) : 'not_found';
        }})()
        '''
        result = qn.js(expr, timeout=30)
        if result and result != "not_found":
            state = json.loads(result).get("state", "")
            if "已完成" in str(state) or "成功" in str(state):
                print(f"  导出完成: {state}")
                break
            if "失败" in str(state):
                raise RuntimeError(f"导出失败: {state}")
        time.sleep(5)

    return task_id


def main():
    parser = argparse.ArgumentParser(description="牵牛花总部商品近30天流量排名下载")
    parser.add_argument("--date", help="数据截止日期 YYYY-MM-DD，默认昨日（仅用于文件名）")
    parser.add_argument("--out", help="输出目录")
    args = parser.parse_args()

    date_str = args.date or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    out_dir = Path(args.out) if args.out else get_raw_dir(date_str)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"{'='*60}")
    print(f"表7：总部商品近30天流量排名下载")
    print(f"数据截止: {date_str}")
    print(f"输出目录: {out_dir}")
    print(f"{'='*60}\n")

    t0 = time.time()
    with QianniuCDP() as qn:
        print(f"已连接: {qn.page_url}")
        task_id = export_hq_30days(qn)
        output_file = out_dir / f"总部商品30天流量排名_{date_str}.xlsx"
        qn.download_task_file(task_id, str(output_file))
        file_size = output_file.stat().st_size if output_file.exists() else 0
        print(f"\n  ✅ 下载完成: {output_file.name} ({file_size/1024:.1f} KB)")

    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"完成，耗时: {elapsed:.1f}秒")
    return 0


if __name__ == "__main__":
    sys.exit(main())
