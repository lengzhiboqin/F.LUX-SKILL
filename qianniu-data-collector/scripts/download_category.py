#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
表5：品类分析数据下载
数据→品类分析→导出（需先选择指标）
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


def export_category_analysis(qn, date_str):
    """导出品类分析数据"""
    # 导航到品类分析
    qn.goto("#/data/category-analysis", settle=5)

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

    # 选择指标（点击"筛选指标"或指标选择器）
    print("  选择导出指标...")
    try:
        # 尝试找到"指标"或"筛选指标"按钮并点击
        expr = '''
        (() => {
            const btns = document.querySelectorAll('button, span, div, a');
            for (const btn of btns) {
                const text = btn.textContent.trim();
                if ((text === '筛选指标' || text === '指标' || text.includes('选择指标')) && btn.offsetParent !== null) {
                    const rect = btn.getBoundingClientRect();
                    if (rect.width > 0 && rect.width < 150) {
                        return JSON.stringify([rect.x+rect.width/2, rect.y+rect.height/2]);
                    }
                }
            }
            return null;
        })()
        '''
        result = qn.js(expr)
        if result:
            xy = json.loads(result)
            qn.click_xy(round(xy[0]), round(xy[1]))
            print("  已打开指标选择器")
            time.sleep(2)

            # 选择"总销售收入"指标
            expr = '''
            (() => {
                const items = document.querySelectorAll('[class*="checkbox"], [class*="item"], label, span, div');
                for (const item of items) {
                    const text = item.textContent.trim();
                    if (text === '总销售收入' && item.offsetParent !== null) {
                        const rect = item.getBoundingClientRect();
                        if (rect.width > 0) {
                            return JSON.stringify([rect.x+rect.width/2, rect.y+rect.height/2]);
                        }
                    }
                }
                return null;
            })()
            '''
            result = qn.js(expr)
            if result:
                xy = json.loads(result)
                qn.click_xy(round(xy[0]), round(xy[1]))
                print("  已选择：总销售收入")
                time.sleep(1)

            # 点击"确定"或"确认"
            expr = '''
            (() => {
                const btns = document.querySelectorAll('button, span, div');
                for (const btn of btns) {
                    const text = btn.textContent.trim();
                    if ((text === '确定' || text === '确认' || text === '完成') && btn.offsetParent !== null) {
                        const rect = btn.getBoundingClientRect();
                        if (rect.width > 0 && rect.width < 100) {
                            return JSON.stringify([rect.x+rect.width/2, rect.y+rect.height/2]);
                        }
                    }
                }
                return null;
            })()
            '''
            result = qn.js(expr)
            if result:
                xy = json.loads(result)
                qn.click_xy(round(xy[0]), round(xy[1]))
                print("  已确认指标选择")
                time.sleep(2)
        else:
            print("  未找到指标选择器，尝试直接导出")
    except Exception as e:
        print(f"  指标选择跳过: {e}")

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
            if "品类" in name:
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
    parser = argparse.ArgumentParser(description="牵牛花花品类分析数据下载")
    parser.add_argument("--date", help="数据日期 YYYY-MM-DD，默认昨日")
    parser.add_argument("--out", help="输出目录")
    args = parser.parse_args()

    date_str = args.date or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    out_dir = Path(args.out) if args.out else get_raw_dir(date_str)
    output_file = out_dir / f"品类分析_{date_str}.xlsx"

    print(f"{'='*60}")
    print(f"表5：品类分析数据下载")
    print(f"日期: {date_str}")
    print(f"输出: {output_file}")
    print(f"{'='*60}\n")

    t0 = time.time()
    with QianniuCDP() as qn:
        print(f"已连接: {qn.page_url}")
        task_id = export_category_analysis(qn, date_str)
        qn.download_task_file(task_id, str(output_file))

    file_size = output_file.stat().st_size if output_file.exists() else 0
    elapsed = time.time() - t0

    print(f"\n✅ 下载完成: {output_file}")
    print(f"   文件大小: {file_size/1024:.1f} KB")
    print(f"   耗时: {elapsed:.1f}秒")

    return 0


if __name__ == "__main__":
    sys.exit(main())
