#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""美团闪购品牌方（连锁账号 shangoue.meituan.com）报表下载器。

通过 CDP 直连已登录的 Chromium，用真实点击在「经营指导 → 报表下载」中导出报表。
全部交互细节均经 2026-09-15 实测核对。

用法:
  # 探测登录态与表单
  python3 shangou_report_download.py probe

  # daily 模式：不碰日期控件，用平台预填日期（普通表=昨日，评价=前日）
  # 原始表默认落 $SHANGOU_DATA_DIR/raw 或 ~/shangou-data/raw，可用 --out 覆盖
  python3 shangou_report_download.py all --daily --timeout 180

  # 单表 daily
  python3 shangou_report_download.py download --report 门店财务明细 --daily

  # 单表指定日期（gen1 日历，跨月/自定义场景，本脚本主要保证 daily；指定日期为 best-effort）
  python3 shangou_report_download.py download --report 商品数据 --start 2026-09-14 --end 2026-09-14

实测要点:
  - 报表表单在 name=hashframe 的 iframe 内。
  - 4 个分类 tab + radio；流量两表是 gen2 表单（统计方式/粒度/时间周期），其余为 gen1 日历表单。
  - gen1 必须勾选至少一个业务指标，否则 toast「请至少勾选一个非默认数据指标」→ 统一点「全选」。
  - gen2 流量表 daily：日期=分天、数据粒度=分门店、时间周期=昨日（文件名带 _分天_分门店_）。
  - 下载按钮有三种 DOM（div.download-btn / a.down-text / 无类 button），已用类名+精确文本统一匹配。
  - 点下载后两种导出模式自动分流：task=去「下载列表」轮询行内 a.a-default-color 下载；direct=点「我知道了」后浏览器直接下载。
  - 文件为 GBK 编码 CSV（嗅探 PK 头则为 xlsx）。
  - 事件型表（问题订单/评价/售后）当日无事件时平台返回「无数据/无有效数据」，属正常 no_data。
"""
import argparse
import csv
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta

from playwright.sync_api import sync_playwright

def _load_cdp_endpoints():
    """CDP端点：环境变量 > 配置文件 > 默认值。
    Windows下9222常被WorkBuddy/Edge占用(mock页)，默认用9333。"""
    # 环境变量
    env_cdp = os.environ.get("SHANGOU_CDP", "")
    if env_cdp:
        return tuple(x.strip() for x in env_cdp.split(",") if x.strip())
    # 配置文件
    cfg_path = os.path.expanduser("~/.config/shangou-pipeline/config.json")
    if os.path.exists(cfg_path):
        try:
            c = json.load(open(cfg_path, encoding="utf-8"))
            port = c.get("cdp_port")
            host = c.get("cdp_host", "127.0.0.1")
            if port:
                return (f"http://{host}:{port}",)
        except Exception:
            pass
    # 默认：Windows用9333(避开WorkBuddy内置Edge 9222)，Linux用9223→9222
    if platform.system() == "Windows":
        return ("http://127.0.0.1:9333", "http://127.0.0.1:9223", "http://127.0.0.1:9222")
    return ("http://127.0.0.1:9223", "http://127.0.0.1:9222")

import platform
CDP_ENDPOINTS = _load_cdp_endpoints()
REPORT_URL = "https://shangoue.meituan.com/#/page/manageAnalysis/pc#/report"
HOME_URL = "https://shangoue.meituan.com/"
LOGIN_URL = "https://waimaie.meituan.com/"
ANTI_BOT_KEYWORDS = ["加载失败", "点此刷新页面重试", "人机验证", "安全验证", "验证失败"]
COOKIE_VALIDITY_DAYS = 10  # cookie有效期7-15天，取中间值10天提醒


def data_root():
    """数据根目录：环境变量 SHANGOU_DATA_DIR 优先，其次读统一配置，最后用技能目录 data/。
    跨 agent 窗口 / 定时任务运行时，数据统一在此，与当前工作目录无关。"""
    env = os.environ.get("SHANGOU_DATA_DIR")
    if env:
        return env
    # 读统一配置
    cfg = os.path.expanduser("~/.config/shangou-pipeline/config.json")
    if os.path.exists(cfg):
        try:
            c = json.load(open(cfg, encoding="utf-8"))
            if c.get("data_dir"):
                return c["data_dir"]
        except Exception:
            pass
    # 默认：技能目录下 data/（通过 __file__ 推断）
    skill_data = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    return skill_data


def default_raw_dir():
    return os.path.join(data_root(), "raw")


# 8 张报表：tab=分类页签，radio=数据类型 value，match=下载列表文件名匹配关键字
REPORTS = {
    "门店财务明细":   {"tab": "交易数据", "radio": "poiFinanceDetail",       "match": ["门店财务明细"]},
    "门店成交明细":   {"tab": "交易数据", "radio": "poiDataDetail",          "match": ["门店成交明细"]},
    "商品数据":       {"tab": "订单数据", "radio": "product",                "match": ["商品数据"]},
    "问题订单数据":   {"tab": "订单数据", "radio": "order",                  "match": ["问题订单数据"]},
    "流量明细(新)":   {"tab": "流量数据", "radio": "trafficDetail",          "match": ["流量明细数据"]},
    "流量渠道明细(新)":{"tab": "流量数据", "radio": "trafficChannelDetail",  "match": ["流量渠道明细数据"]},
    "评价数据":       {"tab": "服务数据", "radio": "evaluate",               "match": ["评价数据"]},
    "售后订单数据":   {"tab": "服务数据", "radio": "afterSalesOrderDetails", "match": ["售后订单数据"]},
}
# gen2 流量表（无日历，用时间周期）
GEN2_REPORTS = {"流量明细(新)", "流量渠道明细(新)"}
# 事件型：无数据属业务事实，不算失败
EVENT_REPORTS = {"问题订单数据", "评价数据", "售后订单数据"}
ORDER = list(REPORTS.keys())


# ---------------- 浏览器连接 ----------------
def connect_browser(pw):
    import urllib.request
    last = None
    for ep in CDP_ENDPOINTS:
        try:
            urllib.request.urlopen(ep + "/json/version", timeout=3)
        except Exception:
            last = f"{ep} 探活失败"
            continue
        try:
            b = pw.chromium.connect_over_cdp(ep, timeout=15000)
            print(f"[browser] connected via {ep}", flush=True)
            return b
        except Exception as e:
            last = f"{ep} connect 失败: {str(e)[:100]}"
    raise RuntimeError(f"浏览器 CDP 通道不可用（9223/9222）：{last}")


def find_page(browser):
    # CDP 刚连接时 contexts.pages 枚举可能有延迟，且页面可能正在跳转中。
    # 多次重试，只返回已登录（不含 login）的 shangoue 页面；含 login 的页面只记录不返回。
    login_page = None
    for attempt in range(10):
        for ctx in browser.contexts:
            for pg in ctx.pages:
                if "shangoue.meituan.com" in pg.url and "login" not in pg.url and "waimaie" not in pg.url:
                    return pg
                if "shangoue.meituan.com" in pg.url or "waimaie.meituan.com" in pg.url:
                    login_page = pg
        if attempt < 9:
            time.sleep(2)
    # 没有已登录的现存标签页：如果有登录页，先等它跳转；否则新开页面跳转
    if login_page is not None:
        try:
            human_like_navigate(login_page)  # 模拟人类操作导航，避免风控
        except RuntimeError as e:
            if "[ANTI_BOT_BLOCKED]" in str(e):
                raise  # 反爬拦截，上层处理
            # 其他导航错误，尝试直接跳转
            try:
                login_page.goto(REPORT_URL, wait_until="domcontentloaded", timeout=45000)
                time.sleep(10)
            except Exception:
                pass
        if "shangoue.meituan.com" in login_page.url and "login" not in login_page.url:
            return login_page
        if "login" in login_page.url or "waimaie" in login_page.url:
            raise RuntimeError("闪购登录态已失效，跳转到登录页，需人工重新登录")
    # 没有任何相关标签页，新开一个
    if browser.contexts:
        pg = browser.contexts[0].new_page()
        try:
            human_like_navigate(pg)  # 模拟人类操作导航，避免风控
        except RuntimeError as e:
            if "[ANTI_BOT_BLOCKED]" in str(e):
                raise
            try:
                pg.goto(REPORT_URL, wait_until="domcontentloaded", timeout=45000)
                time.sleep(10)
            except Exception:
                pass
        if "shangoue.meituan.com" in pg.url and "login" not in pg.url:
            return pg
        if "login" in pg.url or "waimaie" in pg.url:
            raise RuntimeError("闪购登录态已失效，跳转到登录页，需人工重新登录")
    raise RuntimeError("未找到 shangoue.meituan.com 页面，请先登录")


def detect_anti_bot(page):
    """检测页面是否被美团反爬/人机识别拦截。
    返回 (is_blocked, reason)。"""
    try:
        body_text = page.locator("body").inner_text(timeout=3000)
    except Exception:
        body_text = ""
    for kw in ANTI_BOT_KEYWORDS:
        if kw in body_text:
            return True, f"页面包含反爬关键词: {kw}"
    # 检查是否有人机识别SDK但页面未正常加载（无hashframe且正文很短）
    has_hashframe = any(f.name == "hashframe" for f in page.frames)
    if not has_hashframe and len(body_text.strip()) < 50 and "shangoue" in page.url:
        return True, "页面未正常加载（无hashframe且正文过短），疑似风控拦截"
    return False, ""


def human_like_navigate(page, target_url=REPORT_URL):
    """模拟人类操作进入商家端首页，再导航到目标页。
    1. 先访问首页，等待页面完全加载
    2. 模拟人类滚动和停留
    3. 再导航到报表页
    这样可以避免直接跳转到深层URL触发风控。"""
    print("[anti-bot] 模拟人类操作导航...")
    # 第一步：访问首页
    page.goto(HOME_URL, wait_until="domcontentloaded", timeout=45000)
    time.sleep(8)  # 等待首页JS完全加载
    # 模拟人类滚动
    try:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight / 3)")
        time.sleep(1)
        page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass
    time.sleep(2)
    # 检查首页是否被风控
    blocked, reason = detect_anti_bot(page)
    if blocked:
        raise RuntimeError(f"[anti-bot] 首页被风控拦截: {reason}")
    # 第二步：再导航到报表页
    print("[anti-bot] 首页正常，导航到报表页...")
    page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
    time.sleep(10)
    # 检查报表页是否被风控
    blocked, reason = detect_anti_bot(page)
    if blocked:
        raise RuntimeError(f"[anti-bot] 报表页被风控拦截: {reason}")
    print("[anti-bot] 导航完成，页面正常")
    return page


def clear_cookies_and_relogin(browser, page):
    """清空cookie，打开商家端登录页，返回登录页page。
    调用方需要请求用户接管浏览器完成登录。"""
    print("[anti-bot] 清空cookie并准备重新登录...")
    ctx = page.context
    # 清空所有cookie和缓存
    ctx.clear_cookies()
    cdp = ctx.new_cdp_session(page)
    try:
        cdp.send('Network.clearBrowserCache')
        cdp.send('Network.clearBrowserCookies')
    except Exception:
        pass
    # 导航到登录页
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
    time.sleep(5)
    print(f"[anti-bot] 登录页已打开: {page.url}")
    return page


def check_cookie_expiry():
    """检查cookie是否即将过期（超过COOKIE_VALIDITY_DAYS天）。
    读取配置文件中的last_login_time，如果超过有效期则返回True。"""
    try:
        config_path = os.path.expanduser("~/.config/shangou-pipeline/config.json")
        if os.path.exists(config_path):
            with open(config_path, encoding="utf-8") as f:
                cfg = json.load(f)
            last_login = cfg.get("last_login_time", "")
            if last_login:
                last_dt = datetime.strptime(last_login, "%Y-%m-%d %H:%M:%S")
                days_since = (datetime.now() - last_dt).days
                if days_since >= COOKIE_VALIDITY_DAYS:
                    print(f"[anti-bot] ⚠️ cookie已使用{days_since}天，接近有效期（{COOKIE_VALIDITY_DAYS}天），建议重新登录")
                    return True
    except Exception:
        pass
    return False


def verify_real_shangou_page(page):
    """校验当前页面是真闪购后台而非 mock/演练页。
    mock 页特征：title=mock、无 hashframe iframe、无真实业务 tab。"""
    title = ""
    try:
        title = page.title()
    except Exception:
        pass
    if title.strip().lower() == "mock":
        raise RuntimeError(
            "检测到 mock/演练页面（页面标题=mock），不是真实闪购商家端。"
            "请在 CDP 浏览器中手动打开 https://shangoue.meituan.com 并真实登录后重试。"
        )
    # 检查 hashframe 是否存在
    has_hashframe = any(f.name == "hashframe" for f in page.frames)
    if not has_hashframe:
        # 等几秒再查（页面可能还在加载）
        for _ in range(5):
            time.sleep(2)
            if any(f.name == "hashframe" for f in page.frames):
                has_hashframe = True
                break
    if not has_hashframe:
        body_text = ""
        try:
            body_text = page.locator("body").inner_text(timeout=3000)[:200]
        except Exception:
            pass
        # 检查是否是反爬拦截（加载失败）
        blocked, reason = detect_anti_bot(page)
        if blocked:
            raise RuntimeError(
                f"[ANTI_BOT_BLOCKED] 美团反爬/人机识别拦截: {reason}。"
                f" 页面正文: {body_text!r}。需要清空cookie重新登录。"
            )
        raise RuntimeError(
            "未检测到报表 iframe（hashframe），当前页面不是闪购报表页。"
            f" 页面标题: {title!r}，页面正文片段: {body_text!r}"
            " 请确认 CDP 浏览器已打开真实闪购商家端并已登录。"
        )
    # 进一步校验 iframe 内是否有真实业务 tab，并确保停留在报表表单页
    fr = None
    for f in page.frames:
        if f.name == "hashframe":
            fr = f
            break
    if fr is not None:
        # 页面可能停在「下载列表」页签：先尝试点击「报表下载」tab回到报表表单页
        for _ in range(2):
            try:
                on_form = fr.evaluate(
                    "() => [...document.querySelectorAll('*')].some(e => "
                    "['交易数据','订单数据','流量数据','服务数据'].includes((e.textContent||'').trim()) "
                    "&& e.children.length < 3)"
                )
                if on_form:
                    break
                # 点击「报表下载」tab（排除「下载列表」）
                clicked = fr.evaluate(
                    "() => {"
                    "  const items = [...document.querySelectorAll('.tab-item, .roo-tabs__nav-item')];"
                    "  for (const it of items) {"
                    "    const t = (it.textContent||'').trim();"
                    "    if (t === '报表下载' && !t.includes('下载列表')) { it.click(); return true; }"
                    "  }"
                    "  return false;"
                    "}"
                )
                if clicked:
                    time.sleep(3)
            except Exception:
                pass
        # 检测任一真实业务tab
        for _ in range(3):
            try:
                tabs = fr.evaluate(
                    "() => [...document.querySelectorAll('*')].filter(e => {"
                    "  const t = (e.textContent||'').trim();"
                    "  return ['交易数据','订单数据','流量数据','服务数据'].includes(t) && e.children.length < 3;"
                    "}).map(e => e.textContent.trim())"
                )
                if tabs:
                    return  # 真实页面，通过
            except Exception:
                pass
            time.sleep(2)
        # iframe 能找到但内容不对
        raise RuntimeError(
            "报表 iframe 存在但未检测到真实业务 tab（如「交易数据」），"
            "可能仍是 mock 页面。请确认浏览器中打开的是真实闪购后台。"
        )


def get_frame(page, timeout_s=45):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for f in page.frames:
            if f.name == "hashframe":
                return f
        time.sleep(1)
    raise RuntimeError("未找到 hashframe iframe（报表页未加载？）")


# ---------------- 通用交互 ----------------
def dismiss_modals(fr, max_rounds=6):
    """关闭 roo-modal 弹窗（真实点击「我知道了/确定/关闭」），兜底移除 backdrop。"""
    btn_sels = (
        "span:text-is('我知道了')", "button:has-text('我知道了')",
        "span:text-is('知道了')", "span:text-is('确定')",
        "span:text-is('关闭')",
    )
    for _ in range(max_rounds):
        nvis = fr.evaluate("""() => [...document.querySelectorAll('.roo-modal.backdrop')].filter(b=>{
            const s=getComputedStyle(b);return s.display!=='none'&&s.visibility!=='hidden';}).length""")
        if not nvis:
            return
        clicked = False
        for sel in btn_sels:
            try:
                fr.locator(sel).first.click(timeout=1500)
                clicked = True
                break
            except Exception:
                continue
        if not clicked:
            fr.evaluate("""()=>[...document.querySelectorAll('.roo-modal.backdrop')].forEach(b=>b.remove())""")
        time.sleep(0.7)


def click_exact_text(fr, text, timeout=8000):
    sel = (f"span:text-is('{text}'), a:text-is('{text}'), li:text-is('{text}'), "
           f"div:text-is('{text}'), label:text-is('{text}')")
    fr.locator(sel).first.click(timeout=timeout)


def form_ready(fr):
    try:
        return fr.evaluate("""() => {
            const btn = document.querySelector('.download-btn') || document.querySelector('.down-text')
              || [...document.querySelectorAll('button,a,div')].find(e=>e.offsetParent!==null
                 && e.children.length<=1 && /^(下载数据|下载报表)$/.test(e.textContent.trim()));
            const hasCal = !!document.querySelector('.ant-calendar-range-picker-input');
            const hasGen2 = !!document.querySelector('.export-time-period-wrap');
            return !!btn && (hasCal || hasGen2)
                && document.querySelectorAll('input[type=radio]').length > 0;
        }""")
    except Exception:
        return False


def go_report_form(page, fr):
    """确保停在「报表下载」表单页（而非下载列表）。"""
    dismiss_modals(fr)
    if form_ready(fr):
        return fr
    try:
        click_exact_text(fr, "报表下载", timeout=5000)
    except Exception:
        pass
    deadline = time.time() + 15
    while time.time() < deadline:
        time.sleep(1.5)
        if form_ready(fr):
            return fr
    # iframe 可能崩溃，导航恢复
    page.goto(REPORT_URL, wait_until="domcontentloaded", timeout=30000)
    time.sleep(7)
    fr = get_frame(page)
    deadline = time.time() + 20
    while time.time() < deadline:
        if form_ready(fr):
            return fr
        time.sleep(1.5)
    raise RuntimeError("无法进入报表下载表单页")


def detect_kind(fr):
    if fr.evaluate("() => !!document.querySelector('.export-time-period-wrap')"):
        return "gen2"
    return "gen1"


def select_radio(fr, value):
    """选中数据类型 radio（真实点击 label）。"""
    for _ in range(4):
        state = fr.evaluate("""(v)=>{
            const r=document.querySelector(`input[type=radio][value='${v}']`);
            if(!r) return null;
            if(r.checked) return 'checked';
            (r.closest('label')||r.parentElement).click();
            return 'clicked';
        }""", value)
        if state == "checked":
            return
        time.sleep(1)
    raise RuntimeError(f"radio {value} 无法选中")


def select_all_metrics(fr):
    """gen1：若存在未选中的业务指标 checkbox，则点「全选」。

    默认列（商家ID/名称/省/市）不算业务指标，平台要求至少勾一个非默认指标。
    """
    state = fr.evaluate("""()=>{
        const cbs=[...document.querySelectorAll('input[type=checkbox]')];
        if(!cbs.length) return 'none';
        // 「全选」label
        const allLab=[...document.querySelectorAll('label.roo-checkbox')].find(l=>l.textContent.trim()==='全选');
        const unchecked=cbs.filter(c=>!c.checked);
        if(unchecked.length===0) return 'all-checked';
        if(allLab){allLab.click(); return 'clicked-select-all';}
        return 'no-select-all';
    }""")
    time.sleep(0.8)
    return state


def config_gen2(fr):
    """gen2 流量表 daily 配置：日期=分天、数据粒度=分门店、时间周期=昨日。"""
    fr.evaluate("""()=>{
        const clickLabel=(t)=>{
            const lab=[...document.querySelectorAll('label')].find(l=>l.textContent.trim()===t);
            if(lab) lab.click();
        };
        clickLabel('分天');
        clickLabel('分门店');
        clickLabel('昨日');
        // 对比数据/商圈同行保持「不导出」
    }""")
    time.sleep(1.0)
    sel = fr.evaluate("""()=>[...document.querySelectorAll('input[type=radio]:checked')]
        .map(r=>(r.closest('label')||r.parentElement).textContent.trim())""")
    required = {"分天", "分门店", "昨日"}
    if not required.issubset(set(sel)):
        raise RuntimeError(f"gen2 粒度配置未生效，当前选中 {sel}")


def read_toast(fr):
    try:
        return fr.evaluate("""()=>{
            const t=document.querySelector('.roo-toast-content');
            return t ? t.textContent.trim() : '';
        }""")
    except Exception:
        return ""


# ---------------- 触发导出 + 下载列表轮询 ----------------
# 下载按钮存在三种 DOM：div.download-btn（下载数据）/ a.down-text（下载报表）/ 无类 button（下载报表）
# 用类名 + 精确文本双保险，文本匹配排除顶部「报表下载/下载列表」页签
DL_BTN_SEL = (".download-btn, .down-text, "
              "button:text-is('下载数据'), button:text-is('下载报表'), "
              "a.down-text:text-is('下载报表'), div.download-btn:text-is('下载数据')")


def click_dismiss_ok(fr):
    """真实点击「我知道了」等确认按钮。"""
    for sel in ("span:text-is('我知道了')", "button:has-text('我知道了')",
                "span:text-is('知道了')", "span:text-is('确定')"):
        try:
            fr.locator(sel).first.click(timeout=1500)
            return True
        except Exception:
            continue
    return False


def detect_export_mode(fr):
    """点击下载按钮并识别导出模式。

    返回:
      'task'   —— 异步任务：「导出任务创建成功…在【下载列表】查看」，需轮询下载列表
      'direct' —— 直接下载：「不会在【下载列表】中展示…浏览器下载记录获取」，点我知道了后直接下载
    点击后保留弹窗不关，交由调用方按模式处理。
    """
    fr.locator(DL_BTN_SEL).first.click(timeout=8000)
    deadline = time.time() + 15
    while time.time() < deadline:
        modal_txt = fr.evaluate("""()=>{
            const m=[...document.querySelectorAll('.roo-modal.backdrop')].find(b=>{
                const s=getComputedStyle(b);return s.display!=='none'&&s.visibility!=='hidden';});
            return m?m.textContent.trim():'';
        }""")
        if modal_txt:
            # direct 模式特征文案优先判定（"不会在下载列表"或"浏览器下载记录"），
            # 避免与 task 模式文案中的"下载列表"关键词混淆导致误判。
            if ("不会在【下载列表】" in modal_txt or "不会在下载列表" in modal_txt
                    or "下载记录" in modal_txt):
                return "direct"
            if "下载列表" in modal_txt and "创建成功" in modal_txt:
                return "task"
            if "无有效数据" in modal_txt or "无数据" in modal_txt:
                raise RuntimeError(f"任务执行无数据: {modal_txt[:60]}")
        toast = read_toast(fr)
        if toast and ("请至少勾选" in toast or "请选择" in toast):
            raise RuntimeError(f"表单校验未通过: {toast}")
        time.sleep(0.7)
    raise RuntimeError("点击下载后未出现导出弹窗（既非任务模式也非直接下载）")


def direct_download(page, fr, timeout_s=60):
    """直接下载模式：在 expect_download 上下文中点掉「我知道了」，捕获浏览器下载。"""
    with page.expect_download(timeout=timeout_s * 1000) as di:
        click_dismiss_ok(fr)
        # 兜底：若首次点击未触发，继续尝试关闭残留弹窗
        for _ in range(8):
            try:
                fr.locator("span:text-is('我知道了')").first.click(timeout=800)
            except Exception:
                pass
            time.sleep(0.5)
    dl = di.value
    tmp = os.path.join(tempfile.gettempdir(), dl.suggested_filename or "direct.csv")
    dl.save_as(tmp)
    return tmp


def goto_download_list(fr):
    click_exact_text(fr, "下载列表", timeout=6000)
    time.sleep(2)


def _row_matches(fname_text, match_keys):
    return all(k in fname_text for k in match_keys)


def poll_and_download(page, fr, report, timeout_s=180):
    """在下载列表轮询匹配行，点击下载并捕获文件，返回临时路径。"""
    keys = REPORTS[report]["match"]
    deadline = time.time() + timeout_s
    tmp_path = None
    while time.time() < deadline:
        rows = fr.evaluate("""()=>[...document.querySelectorAll('tr')].slice(1).map(r=>({
            txt:r.textContent.trim().replace(/\\s+/g,' '),
            hasLink: !!r.querySelector('a.a-default-color, a')
        }))""")
        # 找第一个匹配且带下载链接的行（列表按时间倒序）
        target = None
        for r in rows:
            # 文件名在操作时间之前，取行首部分；直接整行匹配关键字
            if _row_matches(r["txt"], keys) and r["hasLink"]:
                target = r
                break
        # 无数据终态
        for r in rows:
            if _row_matches(r["txt"], keys) and ("无有效数据" in r["txt"] or "失败" in r["txt"]):
                raise RuntimeError(f"导出无数据/失败: {r['txt'][:60]}")
        if target:
            # 用 expect_download 捕获该匹配行的下载链接点击
            loc = fr.locator("tr").filter(has_text=keys[0]).first.locator(
                "a.a-default-color, a").first
            try:
                with page.expect_download(timeout=15000) as di:
                    loc.click()
                dl = di.value
                tmp = os.path.join(tempfile.gettempdir(), dl.suggested_filename or f"{report}.csv")
                dl.save_as(tmp)
                tmp_path = tmp
                break
            except Exception as e:
                if "无数据" in str(e):
                    raise
                # 可能点到旧任务/行未就绪，刷新列表后继续
                time.sleep(2)
        # 刷新列表
        try:
            fr.locator("button:has-text('刷新'), a:has-text('刷新'), span:has-text('刷新')").first.click(timeout=2000)
        except Exception:
            pass
        time.sleep(3)
    if not tmp_path:
        raise RuntimeError(f"下载列表等待超时: {report}")
    time.sleep(1)
    return tmp_path


def sniff_and_save(tmp, out_dir, base_name):
    import shutil
    os.makedirs(out_dir, exist_ok=True)
    with open(tmp, "rb") as f:
        magic = f.read(4)
    ext = ".xlsx" if magic.startswith(b"PK") else ".csv"
    final = os.path.join(out_dir, base_name + ext)
    # shutil.move 兼容 /tmp 与目标目录跨文件系统的情况
    shutil.move(tmp, final)
    return final


def validate_file(path, start, end):
    """返回 (数据行数, 列数)；校验 GBK CSV 可读、日期不越界。"""
    if path.endswith(".xlsx"):
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True)
        ws = wb.active
        return max(ws.max_row - 1, 0), ws.max_column
    with open(path, encoding="gbk", errors="replace", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        raise RuntimeError("空文件")
    ncols = len(rows[0])
    d0 = datetime.strptime(start, "%Y-%m-%d").date()
    d1 = datetime.strptime(end, "%Y-%m-%d").date()
    want = {(d0 + timedelta(days=i)).strftime("%Y%m%d")
            for i in range((d1 - d0).days + 1)}
    pat = re.compile(r"(?<!\d)(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})(?!\d)")
    pat_bare = re.compile(r"(?<!\d)(20\d{6})(?!\d)")
    got = set()
    for r in rows[1:]:
        c = (r[0] if r else "").strip().lstrip("﻿")
        m = pat.search(c)
        if m:
            got.add(f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}")
        else:
            m = pat_bare.match(c)
            if m:
                got.add(m.group(1))
    if got and not (got <= want):
        raise RuntimeError(f"日期不符: 文件含 {got}, 请求 {want}")
    return len(rows) - 1, ncols


# ---------------- 单表下载主流程 ----------------
def daily_dates(report):
    """daily 模式的数据日期：普通表=昨日，评价=前日。"""
    if report == "评价数据":
        d = datetime.now() - timedelta(days=2)
    else:
        d = datetime.now() - timedelta(days=1)
    s = d.strftime("%Y-%m-%d")
    return s, s


def download_one(page, report, start, end, out_dir, timeout_s, daily=False):
    fr = get_frame(page)
    fr = go_report_form(page, fr)
    cfg = REPORTS[report]
    kind = "gen2" if report in GEN2_REPORTS else "gen1"

    # gen1 需要先切分类 tab；gen2 流量表也在「流量数据」tab 下
    click_exact_text(fr, cfg["tab"], timeout=8000)
    time.sleep(1.8)
    dismiss_modals(fr)
    select_radio(fr, cfg["radio"])
    time.sleep(1.5)

    if kind == "gen2":
        config_gen2(fr)
    else:
        # gen1：daily 不碰日期；确保业务指标全选
        st = select_all_metrics(fr)
        print(f"  [{report}] 指标选择: {st}", flush=True)

    # 读取平台预填日期用于归档/校验（daily）
    if daily:
        start, end = daily_dates(report)
        print(f"  [{report}] daily 数据日期={start}", flush=True)

    # 触发导出并识别模式
    mode = detect_export_mode(fr)
    if mode == "direct":
        # 直接下载：点「我知道了」后浏览器直接下载
        tmp = direct_download(page, fr, timeout_s=timeout_s)
    else:
        # 异步任务：关闭弹窗 → 下载列表轮询
        dismiss_modals(fr)
        goto_download_list(fr)
        tmp = poll_and_download(page, fr, report, timeout_s=timeout_s)
    base = f"{report}_{start}"
    final = sniff_and_save(tmp, out_dir, base)
    nrows, ncols = validate_file(final, start, end)
    print(f"  [{report}] mode={mode} rows={nrows} cols={ncols} file={os.path.basename(final)}", flush=True)
    return {"file": final, "rows": nrows, "cols": ncols, "kind": kind, "mode": mode, "date": start}


def download_with_retry(page, report, start, end, out_root, timeout_s, daily, attempts=3):
    """带 no_data 终态识别与重试的单表封装。daily 落盘到 out_root/<数据日期>/。"""
    if daily:
        d, _ = daily_dates(report)
        out_dir = os.path.join(out_root, d)
        start = end = d
    else:
        out_dir = out_root
    for attempt in range(1, attempts + 1):
        try:
            return download_one(page, report, start, end, out_dir, timeout_s, daily=daily)
        except RuntimeError as e:
            msg = str(e)
            if ("无数据" in msg) or ("无有效数据" in msg):
                print(f"  [{report}] no_data: {msg[:70]}", flush=True)
                return {"file": None, "rows": 0, "cols": 0, "no_data": True,
                        "note": msg[:80], "date": start}
            if attempt == attempts:
                raise
            print(f"  [{report}] 第{attempt}次失败，重试: {msg[:90]}", flush=True)
            time.sleep(3)


# ---------------- probe ----------------
def cmd_probe(browser):
    page = find_page(browser)
    if "login" in page.url or "waimaie" in page.url:
        print(json.dumps({"logged_in": False, "url": page.url}, ensure_ascii=False, indent=2))
        return
    # 导航到报表页
    if "#/report" not in page.url:
        try:
            human_like_navigate(page)
        except RuntimeError as e:
            if "[ANTI_BOT_BLOCKED]" in str(e):
                # 反爬拦截：清空cookie，打开登录页
                print("[anti-bot] 检测到反爬拦截，清空cookie并打开登录页...")
                clear_cookies_and_relogin(browser, page)
                print(json.dumps({"logged_in": False, "anti_bot": True,
                                   "url": page.url,
                                   "message": "美团反爬拦截，已清空cookie，请重新登录"},
                                  ensure_ascii=False, indent=2))
                return
            raise
    # 防 mock 页校验
    try:
        verify_real_shangou_page(page)
    except RuntimeError as e:
        if "[ANTI_BOT_BLOCKED]" in str(e):
            print("[anti-bot] 检测到反爬拦截，清空cookie并打开登录页...")
            clear_cookies_and_relogin(browser, page)
            print(json.dumps({"logged_in": False, "anti_bot": True,
                               "url": page.url,
                               "message": "美团反爬拦截，已清空cookie，请重新登录"},
                              ensure_ascii=False, indent=2))
            return
        raise
    fr = get_frame(page)
    deadline = time.time() + 25
    while time.time() < deadline and not form_ready(fr):
        time.sleep(1.5)
    info = fr.evaluate("""()=>({
        url: location.href,
        radios: [...document.querySelectorAll('input[type=radio]')].map(r=>r.value),
        dates: [...document.querySelectorAll('.ant-calendar-range-picker-input')].map(i=>i.value),
        gen2: !!document.querySelector('.export-time-period-wrap'),
        hasDownloadBtn: !!(document.querySelector('.download-btn')||document.querySelector('.down-text'))
    })""")
    info["logged_in"] = True
    print(json.dumps(info, ensure_ascii=False, indent=2))


# ---------------- main ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["probe", "download", "all"])
    ap.add_argument("--report")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--out", default=None, help="原始报表输出目录，默认 $SHANGOU_DATA_DIR/raw 或 ~/shangou-data/raw")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--daily", action="store_true", help="用平台预填日期（昨日/评价前日），不碰日期控件")
    args = ap.parse_args()
    if not args.out:
        args.out = default_raw_dir()

    pw = sync_playwright().start()
    browser = connect_browser(pw)
    if args.cmd == "probe":
        try:
            cmd_probe(browser)
        finally:
            pw.stop()
        return

    summary = {}
    anti_bot_triggered = False
    try:
        page = find_page(browser)
        if "login" in page.url or "waimaie" in page.url:
            raise RuntimeError("未登录，请先在浏览器登录闪购商家端")
        if "#/report" not in page.url:
            try:
                human_like_navigate(page)
            except RuntimeError as e:
                if "[ANTI_BOT_BLOCKED]" in str(e):
                    print("[anti-bot] 检测到反爬拦截，清空cookie并打开登录页...")
                    clear_cookies_and_relogin(browser, page)
                    anti_bot_triggered = True
                    raise RuntimeError("[ANTI_BOT] 美团反爬拦截，已清空cookie，请重新登录后重试")
                raise

        # 防 mock 页：确认是真闪购后台才开始下载
        try:
            verify_real_shangou_page(page)
        except RuntimeError as e:
            if "[ANTI_BOT_BLOCKED]" in str(e):
                print("[anti-bot] 检测到反爬拦截，清空cookie并打开登录页...")
                clear_cookies_and_relogin(browser, page)
                anti_bot_triggered = True
                raise RuntimeError("[ANTI_BOT] 美团反爬拦截，已清空cookie，请重新登录后重试")
            raise

        if args.cmd == "download":
            if not args.report:
                raise SystemExit("download 需要 --report")
            r = download_with_retry(page, args.report, args.start, args.end,
                                    args.out, args.timeout, args.daily)
            summary[args.report] = r
        else:  # all
            reports = ORDER
            for rep in reports:
                try:
                    summary[rep] = download_with_retry(
                        page, rep, args.start, args.end, args.out,
                        args.timeout, args.daily)
                except Exception as e:
                    summary[rep] = {"file": None, "failed": True, "note": str(e)[:120]}
                    print(f"  [{rep}] 失败跳过: {str(e)[:100]}", flush=True)
    except RuntimeError as e:
        if "[ANTI_BOT]" in str(e):
            print(f"\n[anti-bot] {e}")
            anti_bot_triggered = True
        else:
            raise
    finally:
        print("\n===== 采集摘要 =====")
        print(json.dumps(summary, ensure_ascii=False, indent=1))
        pw.stop()

    # 反爬拦截：退出码4（需要重新登录）
    if anti_bot_triggered:
        sys.exit(4)
    # 有失败表则退出码 1（no_data 不算失败）
    failed = [k for k, v in summary.items() if v.get("failed")]
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
