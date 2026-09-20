#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
表6：门店营业时长数据下载
数据→经营→门店营业时长→导出
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


def export_store_hours(qn, date_str):
    """导出门店营业时长数据"""
    # 导航到门店营业时长
    qn.goto("#/data/store-hours", settle=5)

    # 滚动到顶部
    qn.js("window.scrollTo(0, 0)", await_promise=False)
    time.sleep(1)

    # 重置日期
    print("  重置日期...")
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

    # 滚动到导出按钮
    qn.js("document.querySelector('.emp-flower-pc-data-export-btn')?.scrollIntoView({block: 'center'})", await_promise=False)
    time.sleep(2)

    # 点击导出
    print("  点击导出按钮...")
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
        # 降级：找文本为"导出"的元素
        expr = '''
        (() => {
            const btns = document.querySelectorAll('button, span, div, a');
            for (const btn of btns) {
                if (btn.textContent.trim() === '导出') {
                    const rect = btn.getBoundingClientRect();
                    if (rect.width > 0 && rect.width < 100) return JSON.stringify([rect.x+rect.width/2, rect.y+rect.height/2]);
                }
            }
            return null;
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
            if "营业时长" in name or "营业" in name:
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
    parser = argparse.ArgumentParser(description="牵牛花花门店营业时长数据下载")
    parser.add_argument("--date", help="数据日期 YYYY-MM-DD，默认昨日")
    parser.add_argument("--out", help="输出目录")
    args = parser.parse_args()

    date_str = args.date or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    out_dir = Path(args.out) if args.out else get_raw_dir(date_str)
    output_file = out_dir / f"门店营业时长_{date_str}.xlsx"

    print(f"{'='*60}")
    print(f"表6：门店营业时长数据下载")
    print(f"日期: {date_str}")
    print(f"输出: {output_file}")
    print(f"{'='*60}\n")

    t0 = time.time()
    with QianniuCDP() as qn:
        print(f"已连接: {qn.page_url}")
        task_id = export_store_hours(qn, date_str)
        qn.download_task_file(task_id, str(output_file))

    file_size = output_file.stat().st_size if output_file.exists() else 0
    elapsed = time.time() - t0

    print(f"\n✅ 下载完成: {output_file}")
    print(f"   文件大小: {file_size/1024:.1f} KB")
    print(f"   耗时: {elapsed:.1f}秒")

    return 0


if __name__ == "__main__":
    sys.exit(main())
