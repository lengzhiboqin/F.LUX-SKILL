#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
牵牛花数据历史回溯 - 单进程稳定版
所有表在同一个CDP连接里操作，日期只调一次
"""

import json
import sys
import time
import argparse
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from qn_cdp import QianniuCDP
from common import get_raw_dir


def get_current_date(qn):
    """获取当前日期"""
    result = qn.js('''
    (() => {
        const inputs = document.querySelectorAll('input');
        for (const input of inputs) {
            if (input.value && input.value.match(/^202\\d-\\d{2}-\\d{2}$/)) {
                return input.value;
            }
        }
        return 'unknown';
    })()
    ''')
    return result


def click_prev_day(qn):
    """点击前一日"""
    qn.js('''
    (() => {
        const all = document.querySelectorAll('*');
        for (const el of all) {
            if (el.children.length === 0 && el.textContent.trim() === '前一日') {
                const rect = el.getBoundingClientRect();
                if (rect.width > 0) {
                    el.click();
                    return 'clicked';
                }
            }
        }
        return 'not_found';
    })()
    ''')
    time.sleep(1.5)


def click_next_day(qn):
    """点击后一日"""
    qn.js('''
    (() => {
        const all = document.querySelectorAll('*');
        for (const el of all) {
            if (el.children.length === 0 && el.textContent.trim() === '后一日') {
                const rect = el.getBoundingClientRect();
                if (rect.width > 0) {
                    el.click();
                    return 'clicked';
                }
            }
        }
        return 'not_found';
    })()
    ''')
    time.sleep(1.5)


def set_date_to(qn, target_date):
    """把日期设置到目标日期"""
    print(f"  调整日期到 {target_date}...")
    
    current = get_current_date(qn)
    print(f"  当前日期: {current}")
    
    if current == target_date:
        print("  日期已正确")
        return True
    
    days_diff = (datetime.strptime(current, "%Y-%m-%d") - datetime.strptime(target_date, "%Y-%m-%d")).days
    print(f"  需要点前一日 {days_diff} 次")
    
    for i in range(abs(days_diff) + 10):
        current = get_current_date(qn)
        if current == target_date:
            print(f"  ✅ 已到目标日期: {current}")
            return True
        click_prev_day(qn)
    
    current = get_current_date(qn)
    print(f"  最终日期: {current}")
    return current == target_date


def poll_task(qn, name_keyword, timeout=120):
    """轮询任务中心，等待导出完成"""
    poll_start = time.time()
    while time.time() - poll_start < timeout:
        expr = r'''
        (async () => {
            const resp = await fetch('/api/v1/task/queryTasks', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({pageNo: 1, pageSize: 10})
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
            if name_keyword in name:
                state = str(t.get("state", ""))
                if "已完成" in state or "成功" in state:
                    print(f"  ✅ 导出完成: {name}")
                    return t["id"]
                if "失败" in state:
                    print(f"  ❌ 导出失败: {name} - {state}")
                    return None
                print(f"  ⏳ 任务进行中: {state}")
                break
        
        time.sleep(5)
    
    print(f"  ⏰ 导出超时")
    return None


def export_store_metrics(qn, out_dir, date_str):
    """导出1：门店经营指标（首页）"""
    print("  [1] 门店经营指标...")
    qn.goto("#/data/home/new", settle=5)
    time.sleep(2)
    
    # 滚动到导出按钮
    qn.js("document.querySelector('.emp-flower-pc-data-export-btn')?.scrollIntoView({block: 'center'})", await_promise=False)
    time.sleep(2)
    
    # 点击导出
    qn.js('''
    (() => {
        const btn = document.querySelector('.emp-flower-pc-data-export-btn');
        if (btn) {
            btn.click();
            return 'clicked';
        }
        return 'no_btn';
    })()
    ''')
    time.sleep(3)
    
    # 关闭弹窗
    qn.js('''
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
    ''')
    time.sleep(1)
    
    # 等待任务完成
    task_id = poll_task(qn, "门店", timeout=60)
    if task_id:
        output_file = out_dir / f"门店经营指标_{date_str}.xlsx"
        qn.download_task_file(task_id, str(output_file))
        print(f"  ✅ 已下载: {output_file.name}")
        return True
    return False


def export_store_traffic(qn, out_dir, date_str):
    """导出2：门店流量"""
    print("  [2] 门店流量...")
    qn.goto("#/data/product/flow-overview", settle=5)
    time.sleep(2)
    
    # 点击导出
    qn.js('''
    (() => {
        const btn = document.querySelector('.emp-flower-pc-data-export-btn');
        if (btn) {
            btn.click();
            return 'clicked';
        }
        return 'no_btn';
    })()
    ''')
    time.sleep(3)
    
    # 关闭弹窗
    qn.js('''
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
    ''')
    time.sleep(1)
    
    task_id = poll_task(qn, "流量", timeout=60)
    if task_id:
        output_file = out_dir / f"门店流量_{date_str}.xlsx"
        qn.download_task_file(task_id, str(output_file))
        print(f"  ✅ 已下载: {output_file.name}")
        return True
    return False


def export_product_traffic(qn, out_dir, date_str):
    """导出3：商品流量（三Tab）"""
    print("  [3] 商品流量...")
    qn.goto("#/data/product/flow-performance", settle=5)
    time.sleep(2)
    
    for tab in ["全部", "店内", "店外"]:
        print(f"    Tab: {tab}")
        # 切换Tab
        qn.js(f'''
        (() => {{
            const tabs = document.querySelectorAll('[class*="tab"], [role="tab"], div, span');
            for (const tab of tabs) {{
                if (tab.textContent.trim() === '{tab}') {{
                    const rect = tab.getBoundingClientRect();
                    if (rect.width > 0 && rect.width < 100 && rect.y < 300) {{
                        tab.click();
                        return 'clicked';
                    }}
                }}
            }}
            return 'not_found';
        }})()
        ''')
        time.sleep(2)
        
        # 点击导出
        qn.js('''
        (() => {
            const btn = document.querySelector('.emp-flower-pc-data-export-btn');
            if (btn) {
                btn.click();
                return 'clicked';
            }
            return 'no_btn';
        })()
        ''')
        time.sleep(3)
        
        # 关闭弹窗
        qn.js('''
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
        ''')
        time.sleep(1)
        
        task_id = poll_task(qn, "商品流量", timeout=60)
        if task_id:
            output_file = out_dir / f"商品流量_{tab}_{date_str}.xlsx"
            qn.download_task_file(task_id, str(output_file))
            print(f"    ✅ 已下载: {output_file.name}")
        time.sleep(2)


def export_category(qn, out_dir, date_str):
    """导出4：品类分析"""
    print("  [4] 品类分析...")
    qn.goto("#/data/category-analysis", settle=5)
    time.sleep(2)
    
    # 点击导出
    qn.js('''
    (() => {
        const btn = document.querySelector('.emp-flower-pc-data-export-btn');
        if (btn) {
            btn.click();
            return 'clicked';
        }
        return 'no_btn';
    })()
    ''')
    time.sleep(3)
    
    # 关闭弹窗
    qn.js('''
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
    ''')
    time.sleep(1)
    
    task_id = poll_task(qn, "品类", timeout=60)
    if task_id:
        output_file = out_dir / f"品类分析_{date_str}.xlsx"
        qn.download_task_file(task_id, str(output_file))
        print(f"  ✅ 已下载: {output_file.name}")
        return True
    return False


def export_store_hours(qn, out_dir, date_str):
    """导出5：门店营业时长"""
    print("  [5] 门店营业时长...")
    qn.goto("#/data/store-hours", settle=5)
    time.sleep(2)
    
    # 点击导出
    qn.js('''
    (() => {
        const btn = document.querySelector('.emp-flower-pc-data-export-btn');
        if (btn) {
            btn.click();
            return 'clicked';
        }
        return 'no_btn';
    })()
    ''')
    time.sleep(3)
    
    # 关闭弹窗
    qn.js('''
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
    ''')
    time.sleep(1)
    
    task_id = poll_task(qn, "营业", timeout=60)
    if task_id:
        output_file = out_dir / f"门店营业时长_{date_str}.xlsx"
        qn.download_task_file(task_id, str(output_file))
        print(f"  ✅ 已下载: {output_file.name}")
        return True
    return False


def export_store_product_daily(qn, out_dir, date_str):
    """导出6：门店商品指标明细"""
    print("  [6] 门店商品指标明细...")
    qn.goto("#/data/product/details", settle=8)
    time.sleep(2)
    
    # 切换维度到门店
    qn.js('''
    (() => {
        const all = document.querySelectorAll('*');
        for (const el of all) {
            if (el.children.length === 0 && el.textContent.trim() === '门店') {
                const rect = el.getBoundingClientRect();
                if (rect.width > 0 && rect.y > 200) {
                    el.click();
                    return 'clicked';
                }
            }
        }
        return 'not_found';
    })()
    ''')
    time.sleep(2)
    
    # 点击导出
    qn.js('''
    (() => {
        const btn = document.querySelector('.emp-flower-pc-data-export-btn');
        if (btn) {
            btn.click();
            return 'clicked';
        }
        return 'no_btn';
    })()
    ''')
    time.sleep(3)
    
    # 关闭弹窗
    qn.js('''
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
    ''')
    time.sleep(1)
    
    task_id = poll_task(qn, "商品详情", timeout=60)
    if task_id:
        output_file = out_dir / f"门店商品指标明细_{date_str}.xlsx"
        qn.download_task_file(task_id, str(output_file))
        print(f"  ✅ 已下载: {output_file.name}")
        return True
    return False


def export_delivery(qn, out_dir, date_str):
    """导出7：配送履约数据"""
    print("  [7] 配送履约数据...")
    qn.goto("#/data/delivery/overview/new", settle=5)
    time.sleep(2)
    
    # 点击导出
    qn.js('''
    (() => {
        const btn = document.querySelector('.emp-flower-pc-data-export-btn');
        if (btn) {
            btn.click();
            return 'clicked';
        }
        return 'no_btn';
    })()
    ''')
    time.sleep(3)
    
    # 关闭弹窗
    qn.js('''
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
    ''')
    time.sleep(1)
    
    task_id = poll_task(qn, "配送", timeout=60)
    if task_id:
        output_file = out_dir / f"配送指标明细_{date_str}.xlsx"
        qn.download_task_file(task_id, str(output_file))
        print(f"  ✅ 已下载: {output_file.name}")
        return True
    return False


def main():
    parser = argparse.ArgumentParser(description="牵牛花数据历史回溯 - 单进程稳定版")
    parser.add_argument("--start", required=True, help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end", help="结束日期 YYYY-MM-DD，默认昨日")
    parser.add_argument("--test", action="store_true", help="测试模式，只跑1天")
    args = parser.parse_args()

    start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
    end_date = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else (datetime.now() - timedelta(days=1)).date()
    
    if args.test:
        end_date = start_date
    
    days = (end_date - start_date).days + 1
    print(f"{'='*60}")
    print(f"牵牛花数据历史回溯（单进程稳定版）")
    print(f"日期范围: {start_date} ~ {end_date}")
    print(f"总天数: {days}")
    print(f"{'='*60}")

    with QianniuCDP() as qn:
        print(f"已连接: {qn.page_url}")
        
        # 第一步：设置起始日期
        print("\n第一步：设置起始日期...")
        qn.goto("#/data/home/new", settle=5)
        set_date_to(qn, args.start)
        
        # 第二步：逐日采集
        current_date = start_date
        day_num = 1
        
        while current_date <= end_date:
            date_str = current_date.strftime("%Y-%m-%d")
            out_dir = get_raw_dir(date_str)
            
            # 检查今天是否已经完成了（有至少5个文件就算完成）
            if out_dir.exists() and len(list(out_dir.glob("*.xlsx"))) >= 5:
                print(f"\n第 {day_num}/{days} 天: {date_str} - ⏭️ 已完成，跳过")
                current_date += timedelta(days=1)
                day_num += 1
                continue
            
            print(f"\n{'─'*60}")
            print(f"第 {day_num}/{days} 天: {date_str}")
            print(f"{'─'*60}")
            
            # 跑当天的表（每个都try-catch，一个失败不影响其他）
            tables = [
                ("门店经营指标", export_store_metrics),
                ("门店流量", export_store_traffic),
                ("商品流量", export_product_traffic),
                ("品类分析", export_category),
                ("门店营业时长", export_store_hours),
                ("门店商品指标明细", export_store_product_daily),
                ("配送履约数据", export_delivery),
            ]
            
            success_count = 0
            fail_count = 0
            for table_name, table_func in tables:
                try:
                    table_func(qn, out_dir, date_str)
                    success_count += 1
                except Exception as e:
                    print(f"  ❌ {table_name} 失败: {e}")
                    fail_count += 1
                    # 出错后等2秒，刷新页面恢复一下
                    time.sleep(2)
            
            print(f"\n  本日汇总: {success_count}成功, {fail_count}失败")
            
            # 点后一日到下一天
            if current_date < end_date:
                print(f"\n  导航回首页准备切日...")
                qn.goto("#/data/home/new", settle=5)
                time.sleep(3)
                
                print(f"  点后一日...")
                click_next_day(qn)
                new_date = get_current_date(qn)
                print(f"  新日期: {new_date}")
                
                # 验证日期真的变了
                expected_next = (current_date + timedelta(days=1)).strftime("%Y-%m-%d")
                if new_date != expected_next:
                    print(f"  ⚠️ 日期不对！期望: {expected_next}, 实际: {new_date}")
                    print(f"  重新导航并点击...")
                    qn.goto("#/data/home/new", settle=5)
                    time.sleep(2)
                    click_next_day(qn)
                    new_date = get_current_date(qn)
                    print(f"  重试后日期: {new_date}")
            
            current_date += timedelta(days=1)
            day_num += 1

    print(f"\n{'='*60}")
    print(f"完成！")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
