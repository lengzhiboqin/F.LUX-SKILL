# -*- coding: utf-8 -*-
"""连接本机 Edge 的 CDP

Edge 启动方式（远程调试开关已在 Local State 打开）：
    msedge.exe --remote-debugging-port=9222 --user-data-dir="...\\Edge\\User Data"
注意：必须显式给 --user-data-dir，否则 Edge 153 不会开调试端口。

用法：
    from cdp import QN
    qn = QN()
    print(qn.js("location.href"))
"""
import json, os, threading, time, urllib.request
from websocket import create_connection

# CDP 端点：环境变量 QIANNIU_CDP > 默认。云端/多宿主可用环境变量覆盖，
# 形如 "http://127.0.0.1:9222"（逗号分隔多端点，按序探活；只写 host:port 也认）。
_DEFAULT_HTTP = "http://127.0.0.1:9222"


def _norm_endpoint(x):
    x = (x or "").strip()
    if not x:
        return ""
    return x if "://" in x else "http://" + x


_ENV_HTTP = _norm_endpoint((os.environ.get("QIANNIU_CDP", "").split(",") or [""])[0])
HTTP = _ENV_HTTP or _DEFAULT_HTTP
TARGET_HOST = "qnh.meituan.com"


def _browser_ws():
    d = json.loads(urllib.request.urlopen(HTTP + "/json/version", timeout=8).read())
    return d["webSocketDebuggerUrl"]


class QN:
    def __init__(self, create_if_missing=True, url="https://qnh.meituan.com/"):
        self.ws = create_connection(_browser_ws(), timeout=180, suppress_origin=True,
                                    max_size=None)
        self._id = 0
        self._pending = {}
        self._events = []
        self._lock = threading.Lock()
        threading.Thread(target=self._read_loop, daemon=True).start()
        self.session = self._find_or_open(url, create_if_missing)

    # ---------- 底层 ----------
    def _read_loop(self):
        while True:
            try:
                msg = json.loads(self.ws.recv())
            except Exception:
                return
            with self._lock:
                if "id" in msg:
                    self._pending[msg["id"]] = msg
                else:
                    self._events.append(msg)

    def call(self, method, params=None, session=None, timeout=180):
        self._id += 1
        mid = self._id
        msg = {"id": mid, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        self.ws.send(json.dumps(msg))
        end = time.time() + timeout
        while time.time() < end:
            with self._lock:
                if mid in self._pending:
                    r = self._pending.pop(mid)
                    if "error" in r:
                        raise RuntimeError(f"CDP {method} -> {r['error']}")
                    return r.get("result", {})
            time.sleep(0.02)
        raise TimeoutError(method)

    # ---------- 页面 ----------
    def _attach(self, target_id):
        return self.call("Target.attachToTarget",
                         {"targetId": target_id, "flatten": True})["sessionId"]

    def _find_or_open(self, url, create_if_missing):
        for p in self.call("Target.getTargets")["targetInfos"]:
            if p.get("type") == "page" and TARGET_HOST in p.get("url", ""):
                return self._attach(p["targetId"])
        if not create_if_missing:
            raise RuntimeError("没有 qnh 页面")
        tid = self.call("Target.createTarget", {"url": url})["targetId"]
        sid = self._attach(tid)
        time.sleep(4)
        return sid

    def new_tab(self, url, wait=3, background=False):
        tid = self.call("Target.createTarget",
                        {"url": url, "background": background})["targetId"]
        sid = self._attach(tid)
        time.sleep(wait)
        return sid

    def navigate(self, url, session=None, wait=10):
        """在现有标签页里跳转（不开新标签页）"""
        s = session or self.session
        self.call("Page.navigate", {"url": url}, s)
        time.sleep(wait)
        return s

    def dismiss_modal(self, session=None, verbose=False):
        """点掉「导出任务创建成功」这类弹窗（优先点「完成」，其次点右上角 X）"""
        s = session or self.session
        return self.js(r'''(() => {
          const modals = [...document.querySelectorAll('.ant-modal,.ant-modal-wrap,[class*=modal]')]
            .filter(m => m.offsetParent && m.querySelector('button'));
          if (!modals.length) return 'no modal';
          for (const m of modals) {
            const btns = [...m.querySelectorAll('button')];
            const done = btns.find(b => /^\s*(完\s*成|我知道了|关闭|确\s*定)\s*$/.test(b.innerText||''));
            if (done) { done.click(); return 'clicked: ' + (done.innerText||'').trim(); }
            const x = m.querySelector('.ant-modal-close,[class*=close]');
            if (x) { x.click(); return 'clicked close X'; }
          }
          return 'modal found but no dismiss button';
        })()''', s)

    def wait_and_dismiss(self, session=None, tries=8, gap=2):
        """弹窗可能延迟出现，轮询几秒直到点掉"""
        for _ in range(tries):
            r = self.dismiss_modal(session)
            if r and not str(r).startswith('no modal'):
                return r
            time.sleep(gap)
        return 'no modal'

    def qnh_tabs(self):
        return [p for p in self.call("Target.getTargets")["targetInfos"]
                if p.get("type") == "page" and TARGET_HOST in p.get("url", "")]

    def keep_only_one_qnh_tab(self):
        """只留最后一个 qnh 标签页，其余关掉"""
        tabs = self.qnh_tabs()
        for p in tabs[:-1]:
            try:
                self.call("Target.closeTarget", {"targetId": p["targetId"]}, timeout=10)
            except Exception:
                pass
        n = len(self.qnh_tabs())
        print(f"[清理] qnh 标签页 {len(tabs)} → {n}")
        return n

    def rebuild_tab(self, url="https://qnh.meituan.com/home.html#/operation/task?queryType=TAB_DOWNLOAD"):
        """把卡死的标签页全部关掉，重开一个干净的（渲染线程卡死时唯一有效的办法）"""
        for p in self.qnh_tabs():
            try:
                self.call("Target.closeTarget", {"targetId": p["targetId"]}, timeout=15)
            except Exception:
                pass
        time.sleep(2)
        tid = self.call("Target.createTarget", {"url": url})["targetId"]
        self.session = self._attach(tid)
        time.sleep(10)
        return self.session

    def close_tab(self, target_id):
        try:
            self.call("Target.closeTarget", {"targetId": target_id}, timeout=10)
        except Exception:
            pass

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass

    # ---------- JS ----------
    def js(self, expr, session=None, timeout=180):
        r = self.call("Runtime.evaluate",
                      {"expression": expr, "awaitPromise": True,
                       "returnByValue": True, "userGesture": True},
                      session or self.session, timeout)
        if "exceptionDetails" in r:
            return "JS异常: " + json.dumps(r["exceptionDetails"], ensure_ascii=False)[:900]
        return r.get("result", {}).get("value")

    def jsj(self, expr, session=None, timeout=180):
        v = self.js(expr, session, timeout)
        if isinstance(v, str):
            if v.startswith("JS异常"):
                raise RuntimeError(v)
            try:
                return json.loads(v)
            except Exception:
                return v
        return v

    def goto(self, hash_path, session=None, wait=6):
        s = session or self.session
        self.js(f"location.hash = {json.dumps(hash_path)}", s)
        time.sleep(wait)

    # ---------- 网络 ----------
    def enable_network(self, session=None):
        self.call("Network.enable", {}, session or self.session)

    def clear_events(self):
        with self._lock:
            self._events.clear()

    def collect(self, url_keyword="downloadTable", session=None):
        s = session or self.session
        with self._lock:
            evs = list(self._events)
        reqs, status = {}, {}
        for e in evs:
            if e.get("sessionId") and e["sessionId"] != s:
                continue
            m, p = e.get("method"), e.get("params", {})
            if m == "Network.requestWillBeSent":
                reqs[p["requestId"]] = {
                    "url": p["request"]["url"],
                    "method": p["request"]["method"],
                    "postData": p["request"].get("postData"),
                }
            elif m == "Network.responseReceived":
                status[p["requestId"]] = p["response"].get("status")
        out = []
        for rid, r in reqs.items():
            if url_keyword in r["url"]:
                r["status"] = status.get(rid)
                out.append(r)
        return out
