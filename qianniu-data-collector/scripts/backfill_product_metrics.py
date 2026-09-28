#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量采集：门店商品指标明细（36个指标全选）
API方式，不需要页面操作，速度快
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
MANIFEST_FILE = DATA_DIR / "manifest.json"

# 门店商品指标明细配置
TABLE_CONFIG = {
    "name": "门店商品指标明细",
    "viewCode": "fusion_product_category_all_in_one_view_new",
    "api_url": "/goldengateway/empower/complexModule/downloadTable",
    "date_fields": ["beginDate", "endDate"],
    "date_format": "%Y%m%d",
    "task_name_prefix": "导出商品详情",
    "filename": "门店商品指标明细",
    "min_rows": 40,
    "max_rows": 100,
    "base_param": {
        "dateType": "d",
        "isSelectAllPoi": True,
        "isSelectAllChannel": False,
        "isSelectAllCatalog": False,
        "isSelectAllFrontCategory": False,
        "dimType": 11,
        "containsCr": False,
        "containsLast": False,
        "filterCode": [],
        "needSummary": "true",
        "order": "",
        "selectedDataCodes": [
            "total_prod_sale_amt", "all_sale_income", "prod_sale_amt", "sale_income",
            "all_actual_pay_amt", "refund_amt", "category_sale_amt_pct", "avg_category_sale_amt",
            "poi_prod_charge_fee", "poi_ord_charge_fee", "poi_up_charge_fee",
            "flat_prod_charge_fee", "flat_ord_charge_fee", "flat_up_charge_fee",
            "org_prod_unit_price",
            "prod_sale_num", "txn_sku_num", "non_txn_sku_num", "txn_sku_rate",
            "avg_prod_sale_num", "category_sale_num_pct", "refund_prod_cnt",
            "eff_ord_cnt", "refund_ord_cnt",
            "total_sku_num", "online_sku_num", "onsale_sku_num", "act_sku_num",
            "stockout_cost", "stockout_sku_num", "unsale_stock_sku_num", "sellout_sku_num",
            "stockout_sku_num_top80", "stockout_amt_top80", "stockout_interval_top80",
            "level_a_sku_stock_rate", "poi_name", "poi_id"
        ],
        "page": 1,
        "pageSize": 10,
        "categoryLevel": 1
    }
}

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
# Manifest（断点续传）
# ============================================================

def load_manifest():
    if MANIFEST_FILE.exists():
        with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_manifest(manifest):
    with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


# ============================================================
# 单天采集
# ============================================================

def collect_one_day(qn, date_str):
    """采集某一天的数据"""
    date_compact = datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y%m%d")
    table_name = TABLE_CONFIG["name"]
    
    # 1. 提交导出请求
    param = TABLE_CONFIG["base_param"].copy()
    param[TABLE_CONFIG["date_fields"][0]] = date_compact
    param[TABLE_CONFIG["date_fields"][1]] = date_compact
    
    result = qn.js(f'''
    (async () => {{
        const resp = await fetch('{TABLE_CONFIG["api_url"]}', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{
                viewCode: '{TABLE_CONFIG["viewCode"]}',
                param: {json.dumps(param, ensure_ascii=False)}
            }})
        }});
        const data = await resp.json();
        return JSON.stringify(data);
    }})()
    ''', timeout=30)
    
    resp_data = json.loads(result)
    if resp_data.get("code") != 0:
        raise RuntimeError(f"提交失败: {resp_data}")
    
    # 2. 等待任务完成
    task_id = None
    poll_start = time.time()
    while time.time() - poll_start < 120:
        tasks_raw = qn.js(r'''
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
        ''', timeout=30)
        
        tasks = json.loads(tasks_raw)
        for t in tasks:
            name = str(t.get("name", ""))
            if TABLE_CONFIG["task_name_prefix"] in name and date_compact in name:
                state = str(t.get("state", ""))
                if "已完成" in state or "成功" in state:
                    task_id = t["id"]
                    break
                if "失败" in state:
                    raise RuntimeError(f"导出失败: {name} - {state}")
        if task_id:
            break
        time.sleep(5)
    
    if not task_id:
        raise RuntimeError("任务超时未完成")
    
    # 3. 下载文件
    out_dir = RAW_DIR / date_str
    out_dir.mkdir(parents=True, exist_ok=True)
    output_file = out_dir / f"{TABLE_CONFIG['filename']}_{date_str}.xlsx"
    
    qn.download_task_file(task_id, str(output_file))
    
    # 4. 校验行数
    import pandas as pd
    df = pd.read_excel(output_file)
    row_count = len(df)
    
    if row_count < TABLE_CONFIG["min_rows"] or row_count > TABLE_CONFIG["max_rows"]:
        print(f"  ⚠️ 行数: {row_count}行 (正常范围: {TABLE_CONFIG['min_rows']}-{TABLE_CONFIG['max_rows']})")
    
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
    print(f"批量采集：门店商品指标明细（36指标全选）")
    print(f"日期范围: {start_date} ~ {end_date}")
    print(f"共{len(dates)}天")
    print(f"{'='*60}\n")
    
    # 加载manifest
    manifest = load_manifest()
    table_key = "门店商品指标明细"
    if table_key not in manifest:
        manifest[table_key] = {}
    
    # 统计进度
    done = 0
    skipped = 0
    failed = 0
    
    t0 = time.time()
    
    with QianniuCDP() as qn:
        print(f"已连接: {qn.page_url}\n")
        
        for i, date_str in enumerate(dates):
            print(f"[{i+1}/{len(dates)}] {date_str}", end=" ... ")
            
            # 检查是否已完成
            if date_str in manifest[table_key] and manifest[table_key][date_str].get("status") == "done":
                print(f"✅ 已完成，跳过 ({manifest[table_key][date_str].get('rows', '?')}行)")
                skipped += 1
                continue
            
            try:
                rows = collect_one_day(qn, date_str)
                manifest[table_key][date_str] = {
                    "status": "done",
                    "rows": rows,
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
                save_manifest(manifest)
                print(f"✅ 成功 ({rows}行)")
                done += 1
            except Exception as e:
                manifest[table_key][date_str] = {
                    "status": "failed",
                    "error": str(e)[:200],
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
                save_manifest(manifest)
                print(f"❌ 失败: {str(e)[:100]}")
                failed += 1
            
            # 请求间隔
            time.sleep(2)
    
    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"批量采集完成！")
    print(f"  新完成: {done}天")
    print(f"  已跳过: {skipped}天")
    print(f"  失败: {failed}天")
    print(f"  总耗时: {elapsed/60:.1f}分钟")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
