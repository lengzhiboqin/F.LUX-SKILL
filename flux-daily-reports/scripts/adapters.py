# -*- coding: utf-8 -*-
"""采集适配层：把两个平台各自的采集器收敛到**同一个** `collect_task` 契约。

实施计划 §2 / §4-T2 的要求
- 对外只有 `collect_task(task, config) -> TaskResult`；不把两份平台代码硬拼
- 牵牛花：复用 `qianniu_collect.Collector`（CDP 已登录浏览器）
- 闪购：复用 `shangou_report_download.download_with_retry`（Playwright + CDP）
- **原件逐批留存**：输出目录由 `batch_id` 决定，不再写回固定历史目录
- **关掉跨批缓存跳过**：每批用独立 manifest，`force` 时绝不因「上次采过」而跳过

本模块只在**真正采集时**才导入平台依赖（websocket / playwright），
因此没有浏览器环境也能导入、也能跑契约测试。
"""
import os
import time

from common import contracts as C
from common import dates

LOGIN_HINTS = ("login", "waimaie", "passport", "验证码", "安全验证")


def _is_login_url(url):
    """判断 URL 是否落在登录/验证页。

    注意牵牛花登录成功后会跳 `home.html#/data/home/new?fromSource=loginPage`，
    query 参数里残留 `loginPage` 字样，但实际已是业务首页；只按路径段（剥掉 ?
    之后的 query 参数）匹配，避免误判为未登录。
    """
    if not url:
        return False
    path = url.split("?", 1)[0]
    return any(h in path for h in LOGIN_HINTS)

# 闪购 gen2 流量表：表单用「时间周期」控件且 `config_gen2()` 硬编码点「昨日」，
# 无法按请求日期配置 —— 定向回采时必须单独拒绝（见 `_collect_shangou`）。
SHANGOU_GEN2_TABLES = {"流量明细(新)", "流量渠道明细(新)"}


class MissingDependency(RuntimeError):
    """平台依赖缺失（如未装 playwright/websocket）。"""


class BrowserUnavailable(RuntimeError):
    """CDP 端点不可达 —— 浏览器没起、或没开远程调试端口。"""


class LoginRequired(RuntimeError):
    """平台要求登录/人工处理（登录失效、验证码、反爬拦截）—— **停下请用户接管**。"""


# 命中这些标记即判定为「需人工登录」，而不是普通失败
LOGIN_MARKERS = ("LOGIN_REQUIRED", "COOKIE_CLEAR_BLOCKED", "ANTI_BOT",
                 "未登录", "请先登录", "重新登录", "安全验证", "验证码", "反爬")


def _looks_like_login(msg):
    return any(m in msg for m in LOGIN_MARKERS)


# ---------------- 通用 ----------------
def _sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _result(task, status, *, files=None, evidence=None, error=None, attempts=1):
    return {
        "task_key": task["task_key"],
        "platform": task["platform"],
        "table": task["table"],
        "business_date": task["business_date"],
        "window_start": task.get("window_start"),
        "window_end": task.get("window_end"),
        "scope_id": task["scope_id"],
        "contract_version": task["contract_version"],
        "status": status,
        "files": files or [],
        "evidence": evidence or {},
        "error": error,
        "attempts": attempts,
        "collected_at": dates.today_shanghai(),
    }


def _file_entry(path):
    return {"path": os.path.abspath(path), "bytes": os.path.getsize(path),
            "sha256": _sha256(path)}


def collect_task(task, config):
    """统一入口。`config["collectors"]` 可按平台注入替身（离线测试用）。"""
    hook = (config.get("collectors") or {}).get(task["platform"])
    if hook is not None:
        return hook(task, config)
    try:
        if task["platform"] == "牵牛花":
            return _collect_qianniu(task, config)
        if task["platform"] == "闪购":
            return _collect_shangou(task, config)
    except LoginRequired as e:
        return _result(task, C.S_BLOCKED,
                       error={"code": "LOGIN_REQUIRED", "detail": str(e)[:300],
                              "action": "请在浏览器窗口完成登录（扫码/密码/验证码）后重试"})
    except MissingDependency as e:
        return _result(task, C.S_FAILED,
                       error={"code": "PLATFORM_ERROR", "detail": "依赖缺失：%s" % e})
    except BrowserUnavailable as e:
        return _result(task, C.S_BLOCKED,
                       error={"code": "BROWSER_UNAVAILABLE", "detail": str(e),
                              "action": "请启动带远程调试端口的浏览器并登录平台"})
    except RuntimeError as e:                          # 平台异常：登录类转「需人工」
        msg = str(e)
        if _looks_like_login(msg):
            return _result(task, C.S_BLOCKED,
                           error={"code": "LOGIN_REQUIRED", "detail": msg[:300],
                                  "action": "请在浏览器窗口完成登录后重试"})
        return _result(task, C.S_FAILED,
                       error={"code": "PLATFORM_ERROR",
                              "detail": "%s: %s" % (type(e).__name__, msg)})
    except Exception as e:                            # noqa: BLE001 —— 采集异常必须如实报
        return _result(task, C.S_FAILED,
                       error={"code": "PLATFORM_ERROR",
                              "detail": "%s: %s" % (type(e).__name__, e)})
    return _result(task, C.S_FAILED,
                   error={"code": "PLATFORM_ERROR",
                          "detail": "未知平台：%s" % task["platform"]})


# ---------------- 牵牛花 ----------------
def map_qianniu_rec(task, rec, path_for):
    """把 `Collector.run_one` 的返回映射成 TaskResult（**纯函数，可单测**）。

    `path_for(table, date, channel) -> 期望的原件绝对路径`
    """
    status = rec.get("status")
    if status == "ok":
        p = rec.get("file") or path_for(task["table"], task["business_date"], None)
        if not p or not os.path.exists(p):
            return _result(task, C.S_FAILED,
                           error={"code": "PARSE_FAILED",
                                  "detail": "平台报 ok 但文件不存在：%s" % p})
        return _result(task, C.S_DOWNLOADED, files=[_file_entry(p)],
                       evidence={"platform_task_id": rec.get("taskId"),
                                 "task_name": rec.get("taskName"),
                                 "cols": rec.get("cols"), "md5": rec.get("md5")})
    if status in ("任务未找到",):
        return _result(task, C.S_FAILED,
                       error={"code": "TASK_NOT_FOUND", "detail": rec.get("submit", "")[:200]})
    if status in ("下载失败",):
        return _result(task, C.S_FAILED,
                       error={"code": "EMPTY_DOWNLOAD", "detail": str(rec.get("detail"))[:200]})
    if status in ("列数异常",):
        return _result(task, C.S_BLOCKED,
                       error={"code": "STRUCTURE_CHANGED", "detail": str(rec.get("detail"))[:300]})
    return _result(task, C.S_FAILED,
                   error={"code": "PLATFORM_ERROR", "detail": "任务状态：%s" % status})


def _configure_qianniu(config, raw_dir):
    """把 CDP 地址与输出目录**配置化**（原脚本写死 127.0.0.1:9222 与固定目录）。"""
    try:
        import common.cdp as cdp
        import qianniu_collect as qn
    except ImportError as e:
        raise MissingDependency(str(e))
    cdp.HTTP = "http://%s:%s" % (config.get("cdp_host", "127.0.0.1"),
                                 config.get("cdp_port", 9222))
    qn.BASE_OUT = _path(raw_dir)
    qn.MANIFEST = qn.BASE_OUT / "_manifest.json"
    return qn


def _path(p):
    from pathlib import Path
    return Path(p)


def _collect_qianniu(task, config):
    from run_daily import raw_dir
    rdir = raw_dir(config, config["batch_id"])
    qn = _configure_qianniu(config, rdir)
    cmap = config.get("channel_map", {"100": "100", "200": "200"})
    channel = None
    if task["scope_id"].startswith("channel:"):
        channel = cmap.get(task["scope_id"].split(":", 1)[1])

    if not _cdp_alive(config):
        raise BrowserUnavailable("牵牛花：CDP %s:%s 无响应" %
                                 (config.get("cdp_host"), config.get("cdp_port")))

    col = qn.Collector()
    # 商品流量近30天是滚动快照：run_one 的 day 必须是**提交日**（窗口结束日+1），
    # 窗口才是 [T-30, T-1]；传 business_date(T-1) 会整体早一天（结束日变 T-2）。
    is_goods_flow = (task["table"] == "商品流量_近30天")
    if is_goods_flow and task.get("window_end"):
        submit_day = dates.shift_iso(task["window_end"], 1)
    else:
        submit_day = task["business_date"]
    try:
        if is_goods_flow and config.get("wait_ready"):
            rec = col.run_goods_flow_ready(dates.to_compact(submit_day),
                                          wait_ready=True, verbose=False)
        else:
            rec = col.run_one(task["table"], dates.to_compact(submit_day),
                              verbose=False, channel=channel)
    finally:
        try:
            col.close()
        except Exception:                              # noqa: BLE001
            pass

    def path_for(table, d, ch):
        # 兜底文件名以窗口结束日（task business_date = end_c）命名，与 run_one 输出对齐；
        # 正常情况下 map_qianniu_rec 优先用 rec.file（实际路径），用不到这里。
        p = os.path.join(rdir, table)
        name = "%s_%s%s.xlsx" % (table, dates.to_compact(d), "_ch%s" % ch if ch else "")
        return os.path.join(p, name)

    return map_qianniu_rec(task, rec, path_for)


def _cdp_alive(config):
    import json
    import urllib.request
    url = "http://%s:%s/json/version" % (config.get("cdp_host", "127.0.0.1"),
                                         config.get("cdp_port", 9222))
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            json.loads(r.read())
        return True
    except Exception:                                  # noqa: BLE001
        return False


# ---------------- 闪购 ----------------
def map_shangou_result(task, res):
    """把 `download_with_retry` 的返回映射成 TaskResult（**纯函数，可单测**）。"""
    if res.get("no_data"):
        return _no_data_result(task, res)
    path = res.get("file")
    if not path or not os.path.exists(path):
        return _result(task, C.S_FAILED,
                       error={"code": "EMPTY_DOWNLOAD",
                              "detail": "闪购未产生文件：%s" % path})
    if os.path.getsize(path) == 0:
        return _result(task, C.S_FAILED,
                       error={"code": "ZERO_BYTES", "detail": "0 字节原件：%s" % path})
    return _result(task, C.S_DOWNLOADED, files=[_file_entry(path)],
                   evidence={"mode": res.get("mode"), "rows": res.get("rows"),
                             "cols": res.get("cols"), "data_date": res.get("date")})


def _no_data_result(task, res):
    """闪购「无数据」是**页面明确弹出**的文案 —— 这是可复核证据，但需带证据字段。"""
    return _result(task, C.S_NO_DATA, evidence={
        "no_data_response": str(res.get("note", ""))[:200],
        "detected_at": dates.today_shanghai(),
        "platform": "闪购"})


def _collect_shangou(task, config):
    if task["scope_id"] != "all":
        return _result(task, C.S_BLOCKED,
                       error={"code": "SCOPE_MISMATCH",
                              "detail": "闪购采集暂不支持门店/渠道范围筛选：%s" % task["scope_id"]})
    # 定向历史日回采：gen1 6 张表走 download_with_retry（set_date_range 双闸门已验证）；
    #    gen2 两张流量表（流量明细(新)/流量渠道明细(新)）走 shangou_collect.collect_one
    #    （roo-datepicker「自定义日」+ apply_date 读回确认），v0.2.1 已实测打通。
    #    不再以 UNSUPPORTED_DOWNLOAD 拒绝 gen2 定向回采。
    if config.get("explicit_date"):
        from common import dates as _d
        want = _d.to_compact(task["business_date"])
        print("[shangou] 定向日回采 %s @ %s → 设日期控件 + 日期校验双闸门"
              % (task["table"], want), flush=True)
    try:
        from playwright.sync_api import sync_playwright
        import shangou_report_download as sg
        import shangou_collect as sgc
        sgc.S = sg          # shangou_collect 模块级 S 需指向同一模块（main() 才会赋值）
    except ImportError as e:
        raise MissingDependency(str(e))
    from run_daily import raw_dir
    rdir = raw_dir(config, config["batch_id"])
    os.makedirs(rdir, exist_ok=True)

    # 端点**只有一个来源**：本技能包的 config.json（覆盖采集器自带的默认端口猜测），
    # 否则「预检说通、连接器却连别的端口」。
    endpoint = "http://%s:%s" % (config.get("cdp_host", "127.0.0.1"),
                                 config.get("cdp_port", 9222))
    sg.CDP_ENDPOINTS = (endpoint,)

    if not _cdp_alive(config):
        raise BrowserUnavailable("闪购：CDP %s:%s 无响应" %
                                 (config.get("cdp_host"), config.get("cdp_port")))

    timeout_s = int(config.get("timeout_s", 180))
    daily = not bool(task.get("business_date") and config.get("explicit_date"))
    with sync_playwright() as pw:
        browser = sg.connect_browser(pw)
        page = sg.find_page(browser)
        if _is_login_url(page.url):
            return _result(task, C.S_BLOCKED,
                           error={"code": "LOGIN_REQUIRED",
                                  "detail": "闪购站点未登录：%s" % page.url})
        d = task["business_date"]
        sg.set_target_date(d)          # 让 validate_file 按目标日期校验（兜底闸门）
        try:
            # gen2 流量表定向回采：走 collect_one（roo「自定义日」，apply_date 读回确认）
            if config.get("explicit_date") and task["table"] in SHANGOU_GEN2_TABLES:
                recent = sgc.make_download_sink(page, os.path.join(rdir, ".staging"))
                res = sgc.collect_one(page, task["table"], d, rdir,
                                      timeout_s=timeout_s,
                                      retries=int(config.get("attempts", 1)),
                                      recent=recent)
            else:
                res = sg.download_with_retry(page, task["table"], d, d, rdir,
                                             timeout_s, daily,
                                             attempts=int(config.get("attempts", 1)))
        finally:
            sg.set_target_date(None)
    return map_shangou_result(task, res)


# ---------------- 登录预检 ----------------
def precheck_login(config):
    """两平台登录预检（只读，不动 Cookie）。返回 `{platform: {ok, detail}}`。"""
    out = {}
    alive = _cdp_alive(config)
    out["cdp"] = {"ok": alive,
                  "detail": "CDP %s:%s" % (config.get("cdp_host"), config.get("cdp_port"))}
    if not alive:
        for p in ("牵牛花", "闪购"):
            out[p] = {"ok": False, "detail": "CDP 端点不可达，无法预检登录"}
        return out

    try:
        import json
        import urllib.request
        url = "http://%s:%s/json/list" % (config.get("cdp_host"), config.get("cdp_port"))
        with urllib.request.urlopen(url, timeout=5) as r:
            pages = json.loads(r.read())
    except Exception as e:                             # noqa: BLE001
        for p in ("牵牛花", "闪购"):
            out[p] = {"ok": False, "detail": "读取标签页失败：%s" % e}
        return out

    for p, host in (("牵牛花", "qnh.meituan.com"), ("闪购", "shangoue.meituan.com")):
        hit = [pg for pg in pages if pg.get("type") == "page"
               and host in (pg.get("url") or "")]
        if not hit:
            out[p] = {"ok": False, "detail": "没找到 %s 标签页，请先打开并登录" % host}
        elif any(_is_login_url(pg.get("url")) for pg in hit):
            out[p] = {"ok": False, "detail": "%s 落在登录/验证页，请人工完成登录" % host}
        else:
            out[p] = {"ok": True, "detail": hit[0].get("url")}
    return out
