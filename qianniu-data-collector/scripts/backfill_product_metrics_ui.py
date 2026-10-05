#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量采集：门店商品指标明细（36个指标全选）
页面操作方式，稳定可靠
"""

import json
import time
import base64
import argparse
import urllib.request
from pathlib import Path
from datetime import datetime, timedelta
from websocket import create_connection

# ============================================================
# 配置
# ============================================================

SKILL_DIR = Path("/home/user/.doubao/agent_mode/workspace/.user_skills/qianniu-data-collector")
DATA_DIR = SKILL_DIR / "data"
RAW_DIR = DATA_DIR / "raw"

# ============================================================
# CDP工具
# ============================================================

class QianniuCDP:
    def __init__(self, port=9222, timeout=120):
        self.port = port
        self.timeout = timeout
        self.ws = None
        self._cid = 0
        self._connect()

    def _connect(self):
        resp = urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json")
        pages = json.loads(resp.read().decode())
        page = next((p for p in pages if "qnh.meituan.com" in p.get("url", "")), None)
        if not page:
            raise RuntimeError("未找到牵牛花页面")
        self.page_url = page["url"]
        self.ws = create_connection(page["webSocketDebuggerUrl"], timeout=self.timeout, suppress_origin=True)

    def cmd(self, method, params=None, timeout=None):
        self._cid += 1
        cid = self._cid
        self.ws.send(json.dumps({"id": cid, "method": method, "params": params or {}}))
        deadline = time.time() + (timeout or self.timeout)
        while time.time() < deadline:
            r = json.loads(self.ws.recv())
            if r.get("id") == cid:
                if "error" in r:
                    raise RuntimeError(f"{method}: {r['error']}")
                return r.get("result", {})
        raise TimeoutError(method)

    def js(self, expr, await_promise=True, timeout=None):
        res = self.cmd("Runtime.evaluate", {
            "expression": expr,
            "awaitPromise": await_promise,
            "returnByValue": True,
            "userGesture": True
        }, timeout=timeout)
        if "exceptionDetails" in res:
            raise RuntimeError(str(res["exceptionDetails"])[:800])
        return res.get("result", {}).get("value")

    def goto(self, hash_route, settle=5.0):
        self.js(f'location.hash = {json.dumps(hash_route)}', await_promise=False)
        time.sleep(settle)

    def click_xy(self, x, y, press_delay=0.12):
        for typ, params in (
            ("mouseMoved", {"type": "mouseMoved", "x": x, "y": y}),
            ("mousePressed", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1}),
            ("mouseReleased", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})
        ):
            self.cmd("Input.dispatchMouseEvent", params)
            if typ == "mousePressed":
                time.sleep(press_delay)

    def download_task_file(self, task_id, save_path, timeout=90):
        expr = f'''
        (async () => {{
            const resp = await fetch('/api/v1/task/exportData?taskId={task_id}&taskType=TAB_DOWNLOAD', {{credentials: 'include'}});
            if (!resp.ok) return 'HTTP_' + resp.status;
            const buf = await resp.arrayBuffer();
            const bytes = new Uint8Array(buf);
            let bin = '';
            const CH = 0x8000;
            for (let i = 0; i < bytes.length; i += CH) {{
                bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
            }}
            return btoa(bin);
        }})()
        '''
        b64 = self.js(expr, timeout=timeout)
        if not isinstance(b64, str) or b64.startswith("HTTP_"):
            raise RuntimeError(f"下载失败 task {task_id}: {b64}")
        with open(save_path, "wb") as f:
            f.write(base64.b64decode(b64))
        return save_path

    def close(self):
        try:
            self.ws.close()
        except:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# ============================================================
# 操作函数
# ============================================================

def select_all_metrics(qn):
    """全选所有指标（5个分组）"""
    # 1. 点击指标选择器（第5个select，y=840左右）
    print("  打开指标选择器...")
    result = qn.js("""
    (function() {
        var sels = document.querySelectorAll('.data-ant-select-selector');
        for (var i = 0; i < sels.length; i++) {
            var rect = sels[i].getBoundingClientRect();
            if (rect.width > 200 && rect.y > 800) {
                sels[i].click();
                return 'clicked';
            }
        }
        return 'not_found';
    })()
    """)
    time.sleep(3)
    print(f"  打开指标选择器: {result}")
    
    if result != 'clicked':
        return False
    
    # 2. 点击5个分组的全选
    print("  全选5个分组...")
    result2 = qn.js("""
    (function() {
        var all = document.querySelectorAll('*');
        var count = 0;
        for (var i = 0; i < all.length; i++) {
            if (all[i].children.length === 0 && all[i].textContent.trim() === '全选') {
                var rect = all[i].getBoundingClientRect();
                if (rect.width > 0 && rect.x > 800) {
                    var parent = all[i].parentElement;
                    var checkbox = parent.querySelector('[class*="checkbox"]');
                    if (checkbox) {
                        checkbox.click();
                        count++;
                    }
                }
            }
        }
        return 'clicked ' + count;
    })()
    """)
    time.sleep(1)
    print(f"  全选结果: {result2}")
    
    # 3. 点击确定
    print("  点击确定...")
    result3 = qn.js("""
    (function() {
        var all = document.querySelectorAll('button, span, div');
        for (var i = 0; i < all.length; i++) {
            if (all[i].children.length === 0) {
                var text = all[i].textContent.trim();
                var rect = all[i].getBoundingClientRect();
                if (rect.width > 0 && rect.x > 800 && (text === '确 定' || text === '确定')) {
                    all[i].click();
                    return 'confirmed';
                }
            }
        }
        return 'not_found';
    })()
    """)
    time.sleep(2)
    print(f"  确定结果: {result3}")
    
    return result3 == 'confirmed'


def switch_to_store_dimension(qn):
    """切换维度到门店"""
    result = qn.js("""
    (function() {
        var all = document.querySelectorAll('*');
        // 找"指标明细导出"标题
        var detailTitle = null;
        for (var i = 0; i < all.length; i++) {
            if (all[i].children.length === 0 && all[i].textContent.trim() === '指标明细导出') {
                var rect = all[i].getBoundingClientRect();
                if (rect.width > 0) {
                    detailTitle = rect;
                    break;
                }
            }
        }
        if (!detailTitle) return 'no_detail_title';
        
        // 找"门店"单选按钮
        for (var i = 0; i < all.length; i++) {
            if (all[i].children.length === 0 && all[i].textContent.trim() === '门店') {
                var rect = all[i].getBoundingClientRect();
                if (rect.width > 0 && rect.y > detailTitle.y && rect.y < detailTitle.y + 100) {
                    all[i].click();
                    return 'clicked';
                }
            }
        }
        return 'not_found';
    })()
    """)
    time.sleep(2)
    print(f"  切换门店维度: {result}")
    return result == 'clicked'


def set_date(qn, target_date):
    """设置日期到目标日期（通过前一日/后一日按钮）"""
    today = datetime.now().strftime("%Y-%m-%d")
    days_diff = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(target_date, "%Y-%m-%d")).days
    
    print(f"  目标日期: {target_date}，需往前点 {days_diff} 次前一日")
    
    # 先重置到今天（点后一日15次）
    for i in range(15):
        qn.js("""
        (function() {
            var all = document.querySelectorAll('*');
            for (var i = 0; i < all.length; i++) {
                if (all[i].children.length === 0 && all[i].textContent.trim() === '后一日') {
                    all[i].click();
                    return 'clicked';
                }
            }
            return 'not_found';
        })()
        """, await_promise=False)
        time.sleep(0.3)
    time.sleep(2)
    
    # 往前点前一日
    for i in range(days_diff):
        qn.js("""
        (function() {
            var all = document.querySelectorAll('*');
            for (var i = 0; i < all.length; i++) {
                if (all[i].children.length === 0 && all[i].textContent.trim() === '前一日') {
                    all[i].click();
                    return 'clicked';
                }
            }
            return 'not_found';
        })()
        """, await_promise=False)
        time.sleep(0.5)
    time.sleep(3)
    
    return True


def query_data(qn):
    """点击查询按钮"""
    result = qn.js("""
    (function() {
        var btns = document.querySelectorAll('.data-roo-btn, button');
        for (var i = 0; i < btns.length; i++) {
            if (btns[i].textContent.trim() === '查 询' || btns[i].textContent.trim() === '查询') {
                btns[i].click();
                return 'clicked';
            }
        }
        return 'not_found';
    })()
    """)
    time.sleep(10)
    print(f"  查询: {result}")
    return result == 'clicked'


def click_export(qn):
    """点击导出按钮"""
    result = qn.js("""
    (function() {
        var btn = document.querySelector('.emp-flower-pc-data-export-btn');
        if (btn) {
            btn.click();
            return 'clicked';
        }
        return 'no_btn';
    })()
    """)
    time.sleep(3)
    
    # 关闭弹窗
    qn.js("""
    (function() {
        var btns = document.querySelectorAll('.data-ant-modal-confirm-btns button');
        for (var i = 0; i < btns.length; i++) {
            if (btns[i].textContent.trim() === '完 成' || btns[i].textContent.trim() === '完成') {
                btns[i].click();
                return 'closed';
            }
        }
        return 'no_modal';
    })()
    """)
    time.sleep(1)
    print(f"  导出: {result}")
    return result == 'clicked'


def wait_for_task(qn, date_compact, timeout=120):
    """等待导出任务完成，返回taskId"""
    poll_start = time.time()
    while time.time() - poll_start < timeout:
        tasks_raw = qn.js(r'''
        (async () => {
            const resp = await fetch('/api/v1/task/queryTasks', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({pageNo: 1, pageSize: 5})
            });
            const data = await resp.json();
            const list = (data.data && (data.data.list || data.data.tasks || data.data.records)) || [];
            return JSON.stringify(list.map(t => ({
                id: t.taskId || t.id,
                name: t.taskName || t.name,
                state: t.executingState
            })));
        })()
        ''', timeout=30)
        
        tasks = json.loads(tasks_raw) if tasks_raw else []
        for t in tasks:
            name = str(t.get("name", ""))
            if "商品详情" in name and date_compact in name:
                state = str(t.get("state", ""))
                if "已完成" in state or "成功" in state:
                    print(f"  导出完成: {name} (id={t['id']})")
                    return t["id"]
                if "失败" in state:
                    raise RuntimeError(f"导出失败: {name} - {state}")
        time.sleep(5)
    
    raise TimeoutError("导出任务超时")


# ============================================================
# 单天采集
# ============================================================

def collect_one_day(qn, date_str):
    """采集某一天的数据"""
    date_compact = date_str.replace("-", "")
    
    # 1. 导航到商品详情页
    print("  导航到商品详情页...")
    qn.goto("#/data/product/details", settle=8)
    
    # 2. 设置日期
    print(f"  设置日期到 {date_str}...")
    set_date(qn, date_str)
    
    # 3. 全选36个指标
    select_all_metrics(qn)
    
    # 4. 切换门店维度
    switch_to_store_dimension(qn)
    
    # 5. 查询
    query_data(qn)
    
    # 6. 导出
    click_export(qn)
    
    # 7. 等待任务完成
    print("  等待任务完成...")
    task_id = wait_for_task(qn, date_compact, timeout=120)
    
    # 8. 下载文件
    out_dir = RAW_DIR / date_str
    out_dir.mkdir(parents=True, exist_ok=True)
    output_file = out_dir / f"门店商品指标明细_{date_str}.xlsx"
    
    qn.download_task_file(task_id, str(output_file))
    
    # 9. 校验行数
    import pandas as pd
    df = pd.read_excel(output_file)
    row_count = len(df)
    print(f"  ✅ 下载完成: {row_count}行")
    
    return row_count


# ============================================================
# 主函数
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="批量采集门店商品指标明细")
    parser.add_argument("--start", help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end", help="结束日期 YYYY-MM-DD")
    args = parser.parse_args()
    
    start_date = args.start or "2026-08-01"
    end_date = args.end or "2026-09-21"
    
    # 生成日期列表
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    dates = []
    current = start
    while current <= end:
        dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    
    print(f"{'='*60}")
    print(f"批量采集：门店商品指标明细（页面操作方式）")
    print(f"日期范围: {start_date} ~ {end_date}")
    print(f"共{len(dates)}天")
    print(f"{'='*60}\n")
    
    done = 0
    failed = 0
    t0 = time.time()
    
    with QianniuCDP() as qn:
        print(f"已连接: {qn.page_url}\n")
        
        for i, date_str in enumerate(dates):
            print(f"[{i+1}/{len(dates)}] {date_str}")
            
            try:
                rows = collect_one_day(qn, date_str)
                done += 1
                print(f"  ✅ 成功 ({rows}行)\n")
            except Exception as e:
                print(f"  ❌ 失败: {str(e)[:100]}\n")
                failed += 1
            
            # 请求间隔
            time.sleep(3)
    
    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"批量采集完成！")
    print(f"  成功: {done}天")
    print(f"  失败: {failed}天")
    print(f"  总耗时: {elapsed/60:.1f}分钟")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
