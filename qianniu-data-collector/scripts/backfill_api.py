#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
牵牛花数据历史回溯 - API版
直接通过接口传日期参数，不用在页面上点来点去
"""

import json
import time
import base64
import urllib.request
from datetime import datetime, timedelta
from websocket import create_connection

# ============================================================
# 接口配置
# ============================================================

TABLES = {
    "门店经营指标": {
        "url": "/goldengateway/empower/generic/table/download",
        "body_template": {
            "viewCode": "homepage_not_erp_poi_rank_table_view",
            "param": {
                "poiIds": [1157860,1157861,1206441,1281141,1289746,1203989,1198294,1219640,1321137,1312571,1193915,1327940,1283416,1324228,1287431,1213439,1303069,1292041,1275419,1196981,1275467,1290938,1323973,1315605,1307169,1215600,1215553,1283427,1315647,1338763,1327692,1322282,1277409,1316949,1276778,1274800,1326754,1231506,1288508,1314276,1302679,1307158,1255892,1269969,1184923,1215951,1269738,1269819,1267222,1267915],
                "channelIds": [],
                "franchiseePerspective": 0,
                "isNonSalePoi": 0,
                "page": 1,
                "pageSize": 15,
                "order": "",
                "isSelectAllPoi": False,
                "onlySelected": True,
                "statisticsType": 1,
                "dateType": "r",
                "beginDate": "20260923",
                "endDate": "20260923",
                "exportBtnId": "homepage_not_erp_poi_rank_table_view"
            }
        },
        "date_fields": ["beginDate", "endDate"],
        "date_format": "%Y%m%d",
        "task_name_prefix": "导出首页门店明细",
        "filename": "门店经营指标"
    },
    
    "门店流量排行": {
        "url": "/goldengateway/empower/component/show/downloadTable",
        "body_template": {
            "viewCode": "flow_overview_poi_rank_table",
            "param": {
                "selectedDataCodes": ["poi_view_uv","poi_view_pv","poi_enter_uv","poi_enter_pv","poi_ord_uv_gmv","poi_ord_cnt_gmv","poi_ctr_uv","poi_cvr_uv_gmv","poi_cxr_uv_gmv"],
                "cityIdList": [110100,140100,140900,150100,320600,320900,321000,330100,330500,330700,330800,350100,350900,370100,410300,430100,430200,430300,430700,440100,440300,440500,440600,440900,441300,442000,450300,450900,500100,511000,520100,520300,530900,533100,540100,610900,620100,653100],
                "startDate": "20260923",
                "endDate": "20260923",
                "dateType": "d",
                "queryType": "2",
                "_realDateType": "d",
                "containsCr": False,
                "containsLast": False,
                "comparisonType": "onlyCurrent",
                "page": 1,
                "pageSize": 10,
                "order": "",
                "filterCode": [],
                "exportBtnId": "flow_overview_poi_rank_table"
            }
        },
        "date_fields": ["startDate", "endDate"],
        "date_format": "%Y%m%d",
        "task_name_prefix": "导出门店流量排行",
        "filename": "门店流量排行"
    },
    
    "商品流量_全部": {
        "url": "/goldengateway/empower/component/show/downloadTable",
        "body_template": {
            "viewCode": "goods_flow_analysis_goods_list",
            "param": {
                "page": 1,
                "pageSize": 10,
                "categoryType": "2",
                "sourceType": "1",
                "startDate": "20260923",
                "endDate": "20260923",
                "dateType": "d",
                "queryType": "1",
                "_realDateType": "d",
                "firstCategoryIds": [],
                "secondCategoryIds": [],
                "thirdCategoryIds": [],
                "order": "",
                "exportBtnId": "PRODUCT_FLOW_PERFORMANCE_TABLE_EXPORT",
                "filterCode": ["crValue","crRatio"]
            }
        },
        "date_fields": ["startDate", "endDate"],
        "date_format": "%Y%m%d",
        "task_name_prefix": "导出商品流量分析",
        "filename": "商品流量_全部"
    },
    
    "门店营业时长": {
        "url": "/goldengateway/empower/generic/table/download",
        "body_template": {
            "viewCode": "qnh_store_operating_hours_header_new",
            "param": {
                "startDate": "2026-09-23",
                "endDate": "2026-09-23",
                "order": "avg_poi_rest_dur asc",
                "regionCodes": [1157860,1157861,1206441,1281141,1289746,1203989,1198294,1219640,1321137,1312571,1193915,1327940,1283416,1324228,1287431,1213439,1303069,1292041,1275419,1196981,1275467,1290938,1323973,1315605,1307169,1215600,1215553,1283427,1315647,1338763,1327692,1322282,1277409,1316949,1276778,1274800,1326754,1231506,1288508,1314276,1302679,1307158,1255892,1269969,1184923,1215951,1269738,1269819,1267222,1267915],
                "isSelectAllPoi": True,
                "isSelectAllChannel": True,
                "exportBtnId": "DATA_STORE_HOURS_TABLE_EXPORT",
                "page": 1,
                "pageSize": 10
            }
        },
        "date_fields": ["startDate", "endDate"],
        "date_format": "%Y-%m-%d",
        "task_name_prefix": "导出门店营业时长",
        "filename": "门店营业时长"
    },
}


# ============================================================
# CDP连接
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
        page = next((p for p in pages if "qnh.meituan.com" in p.get("url", "")), pages[0])
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
# 核心功能
# ============================================================

def submit_export(qn, table_name, date_str):
    """提交导出任务"""
    config = TABLES[table_name]
    
    # 格式化日期
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    date_fmt = dt.strftime(config["date_format"])
    
    # 复制模板并替换日期
    body = json.loads(json.dumps(config["body_template"]))
    for field in config["date_fields"]:
        body["param"][field] = date_fmt
    
    # 提交请求
    js_code = f"""
    (async () => {{
        const resp = await fetch('{config["url"]}', {{
            method: 'POST',
            credentials: 'include',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({json.dumps(body)})
        }});
        return JSON.stringify({{ status: resp.status, text: (await resp.text()).slice(0, 200) }});
    }})()
    """
    
    result = qn.js(js_code, await_promise=True, timeout=30)
    return result


def wait_for_task(qn, task_name_prefix, date_str, timeout=120):
    """等待任务完成，返回taskId"""
    # 格式化日期为YYYYMMDD格式（任务名里的格式）
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    date_suffix = dt.strftime("%Y%m%d")
    
    query_code = """
    (async () => {
        const resp = await fetch('/api/v1/task/queryTasks', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
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
    """
    
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = qn.js(query_code, await_promise=True, timeout=30)
        tasks = json.loads(result) if result else []
        
        for t in tasks:
            name = t.get("name", "")
            # 匹配任务名：前缀 + 日期后缀
            if task_name_prefix in name and date_suffix in name:
                state = str(t.get("state", ""))
                if "已完成" in state or "成功" in state:
                    return t["id"]
                if "失败" in state:
                    raise RuntimeError(f"任务失败: {name} - {state}")
        
        time.sleep(3)
    
    raise TimeoutError(f"任务超时: {task_name_prefix} {date_suffix}")


def download_file(qn, task_id, save_path):
    """下载导出文件"""
    download_code = f"""
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
    """
    
    b64 = qn.js(download_code, await_promise=True, timeout=60)
    if not isinstance(b64, str) or b64.startswith("HTTP_"):
        raise RuntimeError(f"下载失败 task {task_id}: {b64}")
    
    with open(save_path, "wb") as f:
        f.write(base64.b64decode(b64))
    
    return save_path


# ============================================================
# 主流程
# ============================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="牵牛花历史回溯 - API版")
    parser.add_argument("--start", default="2026-08-01", help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end", default="2026-09-21", help="结束日期 YYYY-MM-DD")
    parser.add_argument("--tables", nargs="+", default=list(TABLES.keys()), help="要采集的表")
    parser.add_argument("--out", default="/home/user/.doubao/agent_mode/workspace/.user_skills/qianniu-data-collector/data/raw", help="输出目录")
    args = parser.parse_args()
    
    # 生成日期列表
    start_dt = datetime.strptime(args.start, "%Y-%m-%d")
    end_dt = datetime.strptime(args.end, "%Y-%m-%d")
    dates = []
    current = start_dt
    while current <= end_dt:
        dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    
    print(f"开始回溯: {args.start} ~ {args.end}")
    print(f"共 {len(dates)} 天, {len(args.tables)} 张表")
    print(f"预计耗时: 约 {len(dates) * len(args.tables) * 3 / 60:.1f} 分钟")
    print()
    
    total_success = 0
    total_fail = 0
    
    with QianniuCDP() as qn:
        print(f"已连接: {qn.page_url}")
        print()
        
        for date_str in dates:
            print(f"=== {date_str} ===")
            day_success = 0
            
            for table_name in args.tables:
                try:
                    print(f"  {table_name}...", end=" ")
                    
                    # 提交导出
                    submit_export(qn, table_name, date_str)
                    time.sleep(2)
                    
                    # 等待任务完成
                    task_id = wait_for_task(qn, TABLES[table_name]["task_name_prefix"], date_str)
                    
                    # 下载文件
                    date_dir = f"{args.out}/{date_str}"
                    import os
                    os.makedirs(date_dir, exist_ok=True)
                    filename = f"{TABLES[table_name]['filename']}_{date_str}.xlsx"
                    filepath = f"{date_dir}/{filename}"
                    download_file(qn, task_id, filepath)
                    
                    file_size = os.path.getsize(filepath) / 1024
                    print(f"✅ {file_size:.1f} KB")
                    day_success += 1
                    
                    # 请求间隔
                    time.sleep(2)
                    
                except Exception as e:
                    print(f"❌ {str(e)[:50]}")
                    total_fail += 1
            
            print(f"  完成: {day_success}/{len(args.tables)}")
            total_success += day_success
            print()
    
    print(f"{'='*60}")
    print(f"全部完成!")
    print(f"成功: {total_success}, 失败: {total_fail}")
    print(f"输出目录: {args.out}")


if __name__ == "__main__":
    main()
