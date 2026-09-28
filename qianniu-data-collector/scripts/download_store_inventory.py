#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
表7：门店商品库存数据下载
商品→门店商品→导出（SKU×门店维度，含渠道库存）
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


def export_store_inventory(qn, date_str):
    """导出门店商品库存数据"""
    # 导航到门店商品
    qn.goto("#/ocms/product", settle=5)

    # 滚动到顶部
    qn.js("window.scrollTo(0, 0)", await_promise=False)
    time.sleep(1)

    # 查找导出按钮（门店商品页面可能没有日期选择，直接导出当前库存）
    print("  查找导出按钮...")

    # 滚动到导出按钮
    qn.js("document.querySelector('.emp-flower-pc-data-export-btn')?.scrollIntoView({block: 'center'})", await_promise=False)
    time.sleep(2)

    # 点击导出
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

    print(f"  导出按钮位置: ({xy[0]:.0f}, {xy[1]:.0f})")
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

    # 轮询任务（库存数据量大，等待时间加长）
    print("  等待导出任务（库存数据量大，可能需要1-3分钟）...")
    task_id = None
    poll_start = time.time()
    while time.time() - poll_start < 180:
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
            if "商品" in name and ("库存" in name or "门店" in name):
                task_id = t["id"]
                print(f"  找到任务: {name} (id={task_id}, state={t.get('state')})")
                break
        if task_id:
            break
        time.sleep(5)

    if not task_id:
        raise RuntimeError("导出任务未生成")

    # 等待完成（库存数据量大，等待5分钟）
    wait_start = time.time()
    while time.time() - wait_start < 300:
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
            print(f"  状态: {state}")
            if "已完成" in str(state) or "成功" in str(state):
                print(f"  导出完成")
                break
            if "失败" in str(state):
                raise RuntimeError(f"导出失败: {state}")
        time.sleep(10)

    return task_id


def main():
    parser = argparse.ArgumentParser(description="牵牛花花门店商品库存数据下载")
    parser.add_argument("--date", help="数据日期 YYYY-MM-DD（库存为当前快照，日期仅用于归档）")
    parser.add_argument("--out", help="输出目录")
    args = parser.parse_args()

    date_str = args.date or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    out_dir = Path(args.out) if args.out else get_raw_dir(date_str)
    output_file = out_dir / f"门店商品库存_{date_str}.xlsx"

    print(f"{'='*60}")
    print(f"表7：门店商品库存数据下载")
    print(f"日期: {date_str}（库存为当前快照）")
    print(f"输出: {output_file}")
    print(f"{'='*60}\n")

    t0 = time.time()
    with QianniuCDP() as qn:
        print(f"已连接: {qn.page_url}")
        task_id = export_store_inventory(qn, date_str)
        qn.download_task_file(task_id, str(output_file))

    file_size = output_file.stat().st_size if output_file.exists() else 0
    elapsed = time.time() - t0

    print(f"\n✅ 下载完成: {output_file}")
    print(f"   文件大小: {file_size/1024:.1f} KB")
    print(f"   耗时: {elapsed:.1f}秒")

    return 0


if __name__ == "__main__":
    sys.exit(main())
