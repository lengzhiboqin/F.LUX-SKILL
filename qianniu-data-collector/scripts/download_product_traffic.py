#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
表4：商品流量数据下载
数据→流量→商品流量分析→导出（三Tab：全部/店内/店外）
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


def switch_tab(qn, tab_name):
    """切换Tab（全部/店内/店外）"""
    expr = f'''
    (() => {{
        const tabs = document.querySelectorAll('[class*="tab"], [role="tab"], div, span');
        for (const tab of tabs) {{
            if (tab.textContent.trim() === '{tab_name}') {{
                const rect = tab.getBoundingClientRect();
                if (rect.width > 0 && rect.width < 100 && rect.y < 300) {{
                    return JSON.stringify([rect.x+rect.width/2, rect.y+rect.height/2]);
                }}
            }}
        }}
        return null;
    }})()
    '''
    result = qn.js(expr)
    if result:
        xy = json.loads(result)
        qn.click_xy(round(xy[0]), round(xy[1]))
        time.sleep(2)
        return True
    return False


def export_product_traffic(qn, date_str, tab_name):
    """导出商品流量数据（指定Tab）"""
    # 导航到商品流量分析
    qn.goto("#/data/product/flow-performance", settle=5)

    # 滚动到顶部
    qn.js("window.scrollTo(0, 0)", await_promise=False)
    time.sleep(1)

    # 重置日期
    print(f"  重置日期...")
    for i in range(10):
        try:
            expr = '''
            (() => {
                const all = document.querySelectorAll('*');
                for (const el of all) {
                    if (el.children.length === 0 && el.textContent.trim() === '后一日') {
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 0) return JSON.stringify([rect.x+rect.width/2, rect.y+rect.height/2]);
                    }
                }
                return null;
            })()
            '''
            result = qn.js(expr)
            if result:
                xy = json.loads(result)
                qn.click_xy(round(xy[0]), round(xy[1]))
                time.sleep(0.5)
            else:
                break
        except Exception:
            break
    time.sleep(2)

    # 切换到目标日期
    today = datetime.now().strftime("%Y-%m-%d")
    days_diff = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(date_str, "%Y-%m-%d")).days
    print(f"  切换到 {date_str}（前一日 {days_diff} 次）")
    if days_diff > 0:
        for i in range(days_diff):
            try:
                expr = '''
                (() => {
                    const all = document.querySelectorAll('*');
                    for (const el of all) {
                        if (el.children.length === 0 && el.textContent.trim() === '前一日') {
                            const rect = el.getBoundingClientRect();
                            if (rect.width > 0) return JSON.stringify([rect.x+rect.width/2, rect.y+rect.height/2]);
                        }
                    }
                    return null;
                })()
                '''
                result = qn.js(expr)
                if result:
                    xy = json.loads(result)
                    qn.click_xy(round(xy[0]), round(xy[1]))
                    time.sleep(1)
                else:
                    break
            except Exception:
                break
    time.sleep(3)

    # 切换Tab
    print(f"  切换到Tab: {tab_name}")
    switch_tab(qn, tab_name)
    time.sleep(2)

    # 滚动到导出按钮
    qn.js("document.querySelector('.emp-flower-pc-data-export-btn')?.scrollIntoView({block: 'center'})", await_promise=False)
    time.sleep(2)

    # 点击导出
    print(f"  点击导出按钮...")
    expr = '''
    (() => {
        const el = document.querySelector('.emp-flower-pc-data-export-btn');
        if (!el) return null;
        const rect = el.getBoundingClientRect();
        return JSON.stringify([rect.x+rect.width/2, rect.y+rect.height/2]);
    })()
    '''
    result = qn.js(expr)
    if not result:
        raise RuntimeError("未找到导出按钮")

    xy = json.loads(result)
    if xy[1] < 0 or xy[1] > 1080:
        qn.js("document.querySelector('.emp-flower-pc-data-export-btn').scrollIntoView({block: 'center'})", await_promise=False)
        time.sleep(2)
        result = qn.js(expr)
        xy = json.loads(result)

    qn.click_xy(round(xy[0]), round(xy[1]))
    print("  已点击导出")
    time.sleep(3)

    # 关闭弹窗
    try:
        expr = '''
        (() => {
            const btns = document.querySelectorAll('.data-ant-modal-confirm-btns button, .data-ant-modal-confirm-btns span');
            for (const btn of btns) {
                if (btn.textContent.trim() === '完 成' || btn.textContent.trim() === '完成') {
                    const rect = btn.getBoundingClientRect();
                    if (rect.width > 0) return JSON.stringify([rect.x+rect.width/2, rect.y+rect.height/2]);
                }
            }
            return null;
        })()
        '''
        result = qn.js(expr)
        if result:
            close_xy = json.loads(result)
            qn.click_xy(round(close_xy[0]), round(close_xy[1]))
            time.sleep(1)
    except Exception:
        pass

    # 轮询任务
    print(f"  等待导出任务...")
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
            if "商品流量" in name or "流量分析" in name:
                task_id = t["id"]
                print(f"  找到任务: {name} (id={task_id}, state={t.get('state')})")
                break
        if task_id:
            break
        time.sleep(3)

    if not task_id:
        raise RuntimeError("导出任务未生成")

    # 等待完成
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
    parser = argparse.ArgumentParser(description="牵牛花商品流量数据下载")
    parser.add_argument("--date", help="数据日期 YYYY-MM-DD，默认昨日")
    parser.add_argument("--tab", choices=["全部", "店内", "店外", "all"], default="all", help="Tab：全部/店内/店外/all(三个都导出)")
    parser.add_argument("--out", help="输出目录")
    args = parser.parse_args()

    date_str = args.date or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    out_dir = Path(args.out) if args.out else get_raw_dir(date_str)

    tabs = ["全部", "店内", "店外"] if args.tab == "all" else [args.tab]

    print(f"{'='*60}")
    print(f"表4：商品流量数据下载")
    print(f"日期: {date_str}")
    print(f"Tab: {tabs}")
    print(f"{'='*60}\n")

    t0 = time.time()
    with QianniuCDP() as qn:
        print(f"已连接: {qn.page_url}")
        for tab in tabs:
            print(f"\n{'---'*10}")
            print(f"导出Tab: {tab}")
            print(f"{'---'*10}")
            task_id = export_product_traffic(qn, date_str, tab)
            output_file = out_dir / f"商品流量_{tab}_{date_str}.xlsx"
            qn.download_task_file(task_id, str(output_file))
            file_size = output_file.stat().st_size if output_file.exists() else 0
            print(f"  ✅ {tab}: {output_file.name} ({file_size/1024:.1f} KB)")

    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"全部完成，耗时: {elapsed:.1f}秒")
    print(f"输出目录: {out_dir}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
