#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
牵牛花 CDP 通用助手
基于 CATPAW 验证的技术方案：CDP直连 + 页面内fetch + 任务中心导出
"""

import json
import time
import base64
import urllib.request
from websocket import create_connection


class QianniuCDP:
    """牵牛花CDP连接助手"""

    def __init__(self, port=9222, timeout=120):
        self.port = port
        self.timeout = timeout
        self.ws = None
        self._cid = 0
        self._connect()

    def _connect(self):
        """连接到牵牛花页面"""
        resp = urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json")
        pages = json.loads(resp.read().decode())
        # 优先找牵牛花页面
        page = next((p for p in pages if "qnh.meituan.com" in p.get("url", "") and "login" not in p.get("url", "")), None)
        if not page:
            page = next((p for p in pages if "meituan.com" in p.get("url", "")), None)
        if not page:
            page = pages[0]
        self.page_url = page["url"]
        self.ws = create_connection(page["webSocketDebuggerUrl"], timeout=self.timeout, suppress_origin=True)

    def cmd(self, method, params=None, timeout=None):
        """发送CDP命令"""
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
        """在页面内执行JS"""
        res = self.cmd("Runtime.evaluate", {
            "expression": expr,
            "awaitPromise": await_promise,
            "returnByValue": True,
            "userGesture": True
        }, timeout=timeout)
        if "exceptionDetails" in res:
            raise RuntimeError(str(res["exceptionDetails"])[:800])
        return res.get("result", {}).get("value")

    def goto(self, hash_route, settle=3.0):
        """导航到hash路由"""
        self.js(f'location.hash = {json.dumps(hash_route)}', await_promise=False)
        time.sleep(settle)

    def click_xy(self, x, y, press_delay=0.12):
        """模拟真实鼠标点击"""
        for typ, params in (
            ("mouseMoved", {"type": "mouseMoved", "x": x, "y": y}),
            ("mousePressed", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1}),
            ("mouseReleased", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})
        ):
            self.cmd("Input.dispatchMouseEvent", params)
            if typ == "mousePressed":
                time.sleep(press_delay)

    def find_btn(self, text, ymin=0, ymax=900, wmax=200):
        """按文本找可见按钮，返回中心坐标"""
        expr = (
            '(function(){const t=' + json.dumps(text) + ';'
            'const els=[...document.querySelectorAll("button,span,div,a")].filter(e=>'
            'e.textContent.replace(/\\s/g,"")===t&&e.getBoundingClientRect().width>0&&'
            'e.getBoundingClientRect().width<' + str(wmax) + '&&e.getBoundingClientRect().y>' +
            str(ymin) + '&&e.getBoundingClientRect().y<' + str(ymax) + ');'
            'if(!els.length)return null;const b=els[els.length-1].getBoundingClientRect();'
            'return JSON.stringify([b.x+b.width/2,b.y+b.height/2]);})()'
        )
        r = self.js(expr)
        return json.loads(r) if r else None

    def click_btn(self, text, ymin=0, ymax=900, wmax=200, settle=1.5):
        """点击文本按钮"""
        xy = self.find_btn(text, ymin, ymax, wmax)
        if not xy:
            raise RuntimeError(f"按钮未找到: {text}")
        self.click_xy(round(xy[0]), round(xy[1]))
        time.sleep(settle)
        return xy

    def poll_task(self, name_prefix, created_after_ts, timeout=300, interval=5):
        """轮询任务中心，返回 (taskId, status)"""
        expr = r'''
        (async () => {
            const resp = await fetch('/api/v1/task/queryTasks', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({pageNo: 1, pageSize: 10, queryType: 'TAB_DOWNLOAD'})
            });
            const data = await resp.json();
            const list = (data.data && (data.data.list || data.data.tasks || data.data.records)) || [];
            return JSON.stringify(list.map(t => ({
                id: t.id || t.taskId,
                name: t.name || t.taskName,
                status: t.status,
                statusDesc: t.statusDesc,
                createTime: t.createTime
            })));
        })()
        '''
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = self.js(expr, timeout=30)
            tasks = json.loads(raw) if raw else []
            for t in tasks:
                if str(t.get("name", "")).startswith(name_prefix):
                    ct = t.get("createTime")
                    if ct is not None:
                        try:
                            cts = int(ct) / 1000 if int(ct) > 1e12 else int(ct)
                        except Exception:
                            cts = 0
                        if cts >= created_after_ts - 60:
                            return t["id"], str(t.get("status"))
            time.sleep(interval)
        raise TimeoutError(f"任务未出现或超时: {name_prefix}")

    def download_task_file(self, task_id, save_path, timeout=90):
        """从任务中心下载导出文件"""
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
        """关闭连接"""
        try:
            self.ws.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


if __name__ == "__main__":
    with QianniuCDP() as q:
        print(f"已连接: {q.page_url}")
        print(f"标题: {q.js('document.title')}")
