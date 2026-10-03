# -*- coding: utf-8 -*-
"""闪购原始报表**回采**（指定历史日期）。

## 为什么需要本模块

豆包技能包的 `shangou_report_download.py`（911 行）**只能采「昨日 / 前日」**：
它的 `--start` / `--end` 只用于**文件名与事后校验**，**从不写入日期控件**
（逐行核实：全文件只有第 419、816 两处碰日历，**都是「读」**）。
技能包向导里的「回溯采集近30日数据」是**引导占位符** —— 它给出的命令仍是 `--daily`。

本模块补上那一小段，**其余全部复用技能包既有实现**（浏览器连接、反爬处理、
切换分类、选类型、触发导出、任务中心轮询、下载、日期校验）。

## 与技能包的关系

`shangou_report_download.py`（闪购页面操作那 911 行）**已随本包提供**，
就在**同目录**下 —— **不再需要解压任何外部 zip**。
本模块只补「指定历史日期」那一小段，其余全部复用它。

用法：
    python scripts/shangou_collect.py --report 门店财务明细 --date 2026-09-13
    python scripts/shangou_collect.py --all --from 2026-09-12 --to 2026-09-14
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(ROOT, "_claude工作区", "shangou_raw")

TABLES = ["门店财务明细", "门店成交明细", "商品数据", "问题订单数据",
          "流量明细(新)", "流量渠道明细(新)", "评价数据", "售后订单数据"]


# 闪购页面操作逻辑（连接浏览器、反爬、切换分类、选类型、触发导出、
# 任务中心轮询、下载、日期校验）在**同目录**的 `shangou_report_download.py` 里。


# ---------------- 日期控件（本模块的**唯一新增逻辑**）----------------
# ⚠️ 三处 JS 都必须只认**可见**的日历容器：页面上可能残留隐藏的旧容器，
#    取「最后一个」会读到过期年月 → 多翻一个月（实测：商品数据整批落到 7 月）。
JS_PANEL_STATE = """() => {
  const _ps = [...document.querySelectorAll('.ant-calendar-picker-container')]
                .filter(p => p.offsetParent !== null);
  const p = _ps[_ps.length - 1];
  if (!p) return null;
  // 必须锁定**左面板**：容器里有左右两半，全局取会读到右边那一半（比目标晚一个月）
  const box = p.querySelector('.ant-calendar-range-left') || p;
  const g = s => [...box.querySelectorAll(s)].map(e => (e.textContent || '').trim());
  return { years: g('.ant-calendar-year-select'), months: g('.ant-calendar-month-select'),
           disabled: box.querySelectorAll('.ant-calendar-date[disabled]').length,
           header: (box.innerText || '').trim().slice(0, 24) };
}"""

JS_STEP_MONTH = """(dir) => {
  const _ps = [...document.querySelectorAll('.ant-calendar-picker-container')]
                .filter(p => p.offsetParent !== null);
  const p = _ps[_ps.length - 1];
  if (!p) return false;
  const cls = dir < 0 ? '.ant-calendar-prev-month-btn' : '.ant-calendar-next-month-btn';
  const b = p.querySelector('.ant-calendar-range-left ' + cls) || p.querySelector(cls);
  if (!b) return false;
  b.click();
  return true;
}"""

JS_PICK_DAY = """(arg) => {
  const d = arg.d, isoDash = arg.isoDash;
  const _ps = [...document.querySelectorAll('.ant-calendar-picker-container')]
                .filter(p => p.offsetParent !== null);
  const p = _ps[_ps.length - 1];
  if (!p) return 'no-panel';
  const boxes = p.querySelectorAll('.ant-calendar-range-left');
  const box = boxes.length ? boxes[boxes.length - 1] : p;
  const tds = [...box.querySelectorAll('td')];
  // 上下文格的 td 会带 ant-calendar-last-month-cell / ant-calendar-next-month-cell，
  // 其内层 .ant-calendar-date 也可能带 old/next 类 —— 两者都要排掉。
  const sameMonth = td => {
    const t = (td.className || '').toString();
    if (/last-month-cell|next-month-cell|disabled/.test(t)) return false;
    const inner = td.querySelector('.ant-calendar-date');
    const it = inner ? (inner.className || '').toString() : '';
    return !/(^|\\s)(old|next)(\\s|$)/.test(it);
  };
  // 🔴 title 的真实格式是「2026年8月27日」，不是 ISO。
  //    只按日号找会先命中网格开头的**上月残留格**（8 月网格含 7/27~7/31）
  //    → 目标 8/27 被点成 7/27（实测：商品数据 27~31 号全错一个月）。
  const norm = t => t.replace(/年|-/g, '-').replace(/月|-/g, '-').replace('日', '');
  let td = tds.find(t => sameMonth(t) && norm(t.getAttribute('title') || '') === isoDash);
  if (!td) td = tds.find(t => sameMonth(t) && (t.textContent || '').trim() === String(d));
  if (!td) return 'no-cell';
  const cell = td.querySelector('.ant-calendar-date') || td;
  if (cell.getAttribute('disabled') !== null) return 'DISABLED';
  cell.click();
  return 'ok';
}"""

JS_READ_DATES = ("() => [...document.querySelectorAll('.ant-calendar-range-picker-input')]"
                 ".map(i => i.value)")

# 🔴 平台对「当天无有效数据」有**明确弹窗**：「导出任务创建失败 无有效数据…」。
#    技能包的 detect_export_mode 把「直接下载」文案排在判定最前，
#    页面上两个弹窗并存时会先命中前者，**永远看不到这个 no_data 提示**（实测）。
#    所以这里自己判一次，把 no_data 拿回来。
JS_NO_DATA_MODAL = """() => {
  const boxes = [...document.querySelectorAll('.roo-modal.backdrop, .roo-modal, [class*=modal]')];
  for (const m of boxes) {
    const s = getComputedStyle(m);
    if (s.display === 'none' || s.visibility === 'hidden') continue;
    const t = (m.textContent || '').trim();
    if (t.includes('无有效数据') || t.includes('无数据') || t.includes('导出任务创建失败'))
      return t.slice(0, 120);
  }
  return '';
}"""

JS_PANEL_OPEN = """() => {
  const ps = document.querySelectorAll('.ant-calendar-picker-container');
  let n = 0;
  for (const p of ps) if (p.offsetParent !== null) n++;
  return n;
}"""


def close_panel(fr, tries=3):
    """关掉可能残留的日历面板 —— 面板不收会挡住后续点击（实测踩过）。"""
    for _ in range(tries):
        try:
            if fr.evaluate(JS_PANEL_OPEN) == 0:
                return True
        except Exception:
            return True
        try:
            fr.locator("body").press("Escape")
        except Exception:
            pass
        try:
            fr.evaluate("() => document.body.click()")
        except Exception:
            pass
        time.sleep(1)
    try:
        return fr.evaluate(JS_PANEL_OPEN) == 0
    except Exception:
        return True


JS_CLICK_CUSTOM = """() => {
  const w = document.querySelector('.export-time-period-wrap');
  if (!w) return 'no-wrap';
  const r = w.querySelector('input[type=radio][value="-999"]');
  if (r) { r.click(); return 'clicked-radio'; }
  const el = [...w.querySelectorAll('*')].find(e => (e.textContent || '').trim() === '自定义');
  if (el) { el.click(); return 'clicked-text'; }
  return 'not-found';
}"""

# ---- gen2 流量表：用的是另一套控件 roo-datepicker（不是通用的 antd 日历）----
ROO_MONTHS = ["一月", "二月", "三月", "四月", "五月", "六月",
              "七月", "八月", "九月", "十月", "十一月", "十二月"]
ROO_TRIGGER = ".export-custom-datepicker-trigger"
ROO_DISPLAY = ".export-custom-date-display__text"

JS_ROO_STATE = """() => {
  const d = document.querySelector('.roo-datepicker-days');
  if (!d) return null;
  const a = [...d.querySelectorAll('.action-select a')].map(e => (e.textContent || '').trim());
  return { year: a[0] || '', month: a[1] || '' };
}"""

JS_ROO_PREV = """(n) => {
  const d = document.querySelector('.roo-datepicker-days');
  if (!d) return false;
  const b = d.querySelector('.action-pre .roo-icon-chevron-left');
  if (!b) return false;
  for (let i = 0; i < n; i++) b.click();
  return true;
}"""

JS_ROO_PICK = """(d) => {
  const box = document.querySelector('.roo-datepicker-days');
  if (!box) return 'no-panel';
  const tds = [...box.querySelectorAll('tbody td')];
  const hit = tds.find(td => !td.classList.contains('old')
                             && (td.textContent || '').trim() === String(d));
  if (!hit) return 'no-cell(共' + tds.length + ')';
  (hit.querySelector('a') || hit).click();
  return 'ok';
}"""


def set_roo_day(fr, day):
    """gen2 流量表：用「自定义日」选单日。返回显示区文本（用于读回确认）。"""
    y, m, d = int(day[:4]), int(day[5:7]), int(day[8:10])
    fr.locator(ROO_TRIGGER).first.click(timeout=10000)
    time.sleep(2.5)
    fr.evaluate("""() => {
      const t = [...document.querySelectorAll('.custom-panel .roo-btn')]
                  .find(e => (e.textContent || '').trim() === '自定义日');
      if (t) t.click();
    }""")
    time.sleep(1.2)
    for _ in range(30):
        st = fr.evaluate(JS_ROO_STATE)
        if not st or not st["year"]:
            break
        try:
            cy, cm = int(st["year"]), ROO_MONTHS.index(st["month"]) + 1
        except (ValueError, TypeError):
            break
        back = (cy - y) * 12 + (cm - m)
        if back <= 0:
            break
        fr.evaluate(JS_ROO_PREV, min(back, 12))
        time.sleep(1.0)
    pick = fr.evaluate(JS_ROO_PICK, d)
    time.sleep(2)
    text = fr.evaluate("() => (document.querySelector('%s') || {}).innerText || ''" % ROO_DISPLAY)
    return {"read": text.strip(), "pick": pick}


def set_date_range(fr, day, debug=False):
    """把区间日历设成 day ~ day，**并读回确认**。返回实际生效的两个值。

    ⚠️ 月份导航必须**一次一格、每格重读**：早先按差值批量点击会点过头，
    且把「面板月份早于目标」误判成「已到位」—— 商品数据因此整批落到 6~7 月。
    """
    y, m, d = int(day[:4]), int(day[5:7]), int(day[8:10])
    fr.locator(".ant-calendar-range-picker-input").nth(0).click(timeout=10000)
    time.sleep(2.5)

    for _ in range(60):
        st = fr.evaluate(JS_PANEL_STATE)
        if not st or not st["years"]:
            if debug:
                print("     [date] 读不到面板 → 中止导航", flush=True)
            break
        try:
            cy = int(st["years"][0].replace("年", ""))
            cm = int(st["months"][0].replace("月", ""))
        except (ValueError, IndexError):
            break
        delta = (cy - y) * 12 + (cm - m)
        if debug:
            print("     [date] 面板=%d年%d月 目标=%d-%02d delta=%+d"
                  % (cy, cm, y, m, delta), flush=True)
        if delta == 0:
            break
        before_hdr = st.get("header", "")
        # delta > 0：面板比目标**晚** → 往前翻（prev，dir=-1）
        # delta < 0：面板比目标**早** → 往后翻（next，dir=+1）
        if not fr.evaluate(JS_STEP_MONTH, -1 if delta > 0 else 1):
            if debug:
                print("     [date] 翻月按钮点不到 → 中止", flush=True)
            break
        # 等面板标题真的变了再继续 —— 不等就会基于过期读数多点一次（实测差一个月）
        changed = False
        for _w in range(12):
            time.sleep(0.5)
            st2 = fr.evaluate(JS_PANEL_STATE)
            if st2 and st2.get("header") and st2["header"] != before_hdr:
                changed = True
                break
        if debug and not changed:
            print("     [date] 翻月后标题未变化（可能已到边界）", flush=True)

    for i in range(2):
        got = fr.evaluate(JS_PICK_DAY, {"isoDash": "%d-%d-%d" % (y, m, d), "d": d})
        if debug:
            print("     [date] 第%d次点日 %d → %s" % (i + 1, d, got), flush=True)
        time.sleep(1.5)

    close_panel(fr)
    vals = fr.evaluate(JS_READ_DATES)
    if debug:
        print("     [date] 最终读回: %r" % (vals,), flush=True)
    return vals


def _contains_day(read, day):
    """把读回的文本去掉非数字后，判断是否含目标日 YYYYMMDD（兼容多种显示格式）。"""
    return day.replace("-", "") in re.sub(r"\D", "", str(read))


def apply_date(fr, report, day, debug=False):
    """按表类型把日期落到目标日。返回 {"kind", "read", "ok"}。

    gen1（6 张表）用 antd 区间日历；gen2（2 张流量表）用 roo-datepicker 的「自定义日」。
    **读回确认不通过就抛错** —— 静默按默认日期导出是这条管线最危险的失败方式。
    """
    if report in S.GEN2_REPORTS:
        got = fr.evaluate(JS_CLICK_CUSTOM)
        time.sleep(2.5)
        if got == "not-found":
            raise RuntimeError("gen2 流量表未找到「自定义」时间周期选项")
        r = set_roo_day(fr, day)
        if r["pick"] != "ok":
            raise RuntimeError("gen2 选日失败：%s" % r["pick"])
        return {"kind": "gen2", "read": r["read"], "ok": _contains_day(r["read"], day)}
    vals = set_date_range(fr, day, debug=debug)
    return {"kind": "gen1", "read": vals, "ok": vals == [day, day]}


# ---------------- 采集一张表一天 ----------------
def collect_one(page, report, day, out_root, timeout_s=180, retries=3, fresh=False,
                debug=False, recent=None):
    if fresh:
        recover_page(page)
    fr = S.get_frame(page, timeout_s=45)
    fr = S.go_report_form(page, fr)
    close_panel(fr)                     # 上一轮若留下面板会挡住所有点击
    cfg = S.REPORTS[report]
    S.click_exact_text(fr, cfg["tab"], timeout=8000)
    time.sleep(1.8)
    S.dismiss_modals(fr)
    S.select_radio(fr, cfg["radio"])
    time.sleep(1.5)
    if report in S.GEN2_REPORTS:
        S.config_gen2(fr)
    else:
        S.select_all_metrics(fr)
    time.sleep(1)

    # 日期：设置后读回确认，不符就整体重设（最多 2 次）
    res = None
    for dt in range(1, 3):
        before = fr.evaluate(JS_READ_DATES)
        res = apply_date(fr, report, day, debug=debug)
        if res["ok"]:
            break
        print("     日期第%d次未生效（实际 %r，改前 %r），重试" % (dt, res["read"], before),
              flush=True)
        time.sleep(2)
    if not res or not res["ok"]:
        raise RuntimeError("日期未生效：期望 %s，实际 %r" % (day, res["read"]))

    out_dir = os.path.join(out_root, day)
    os.makedirs(out_dir, exist_ok=True)
    last = None
    for attempt in range(1, retries + 1):
        if recent is not None:
            recent.clear()               # 只认本次触发产生的下载
        t0 = time.time()
        try:
            mode = S.detect_export_mode(fr)
            # 🔴 事件型表立刻分流：平台对「当天无有效数据」与「直接下载」两个提示
            #    是**同时出现**的，而技能包按文案优先级先返回 direct，把 no_data 掩掉。
            #    必须在此刻判 —— 等导出超时（45 秒）后弹窗早已消失（实测踩过）。
            #    有数据的当天下载会立刻触发，所以先查监听器，快路径不受影响。
            if report in S.EVENT_REPORTS:
                for _w in range(8):
                    got = take_recent_download(recent, report, day, out_dir, since_ts=t0)
                    if got:
                        return got
                    nd = fr.evaluate(JS_NO_DATA_MODAL)
                    if nd:
                        return {"file": None, "rows": 0, "cols": 0, "no_data": True,
                                "note": nd[:100]}
                    time.sleep(1.5)
            try:
                tmp = _export(page, fr, report, mode, timeout_s)
            except Exception as e:
                # ⓪ 平台说了「无有效数据」→ 这是**平台确认的无事件**，不是失败
                nd = fr.evaluate(JS_NO_DATA_MODAL)
                if nd:
                    return {"file": None, "rows": 0, "cols": 0, "no_data": True, "note": nd[:100]}
                # ① 先看常驻监听器有没有接住（下载可能早于 expect_download 开始）
                got = take_recent_download(recent, report, day, out_dir, since_ts=t0)
                if got:
                    return got
                # ② 事件型表**不走跨通道兜底**：它们体量极小且通道固定
                #    （问题订单/评价=直下，售后=任务中心，见 schema.md），
                #    走错通道只会白等一轮 —— 实测一个失败日要耗 4 分钟。
                if report in S.EVENT_REPORTS:
                    raise
                # ③ 其余表两条通道互相兜底（直下超时最常见）
                other = "task" if mode == "direct" else "direct"
                print("     %s 通道失败（%s），改走 %s" % (mode, str(e)[:60], other), flush=True)
                tmp = _export(page, fr, report, other, timeout_s)
                mode = other
            final = S.sniff_and_save(tmp, out_dir, "%s_%s" % (report, day))
            rows, cols = S.validate_file(final, day, day)
            return {"file": final, "rows": rows, "cols": cols, "mode": mode}
        except Exception as e:                      # 兜住 playwright 超时等非 RuntimeError
            nd = fr.evaluate(JS_NO_DATA_MODAL)
            if nd:
                return {"file": None, "rows": 0, "cols": 0, "no_data": True, "note": nd[:100]}
            got = take_recent_download(recent, report, day, out_dir, since_ts=t0)
            if got:
                return got
            msg = str(e)
            if "无数据" in msg or "无有效数据" in msg:
                return {"file": None, "rows": 0, "cols": 0, "no_data": True, "note": msg[:80]}
            last = "%s: %s" % (type(e).__name__, msg[:160])
            if attempt == retries:
                raise RuntimeError(last)
            print("     第%d次失败，重试：%s" % (attempt, last[:100]), flush=True)
            time.sleep(5)
    raise RuntimeError(last or "未知失败")


def make_download_sink(page, staging_dir):
    """挂一个常驻的下载监听器，把浏览器下载落到暂存目录。

    🔴 为什么需要：技能包的 `direct_download` 是「**先**开始监听 → **再**点确认」，
    但平台的下载是在**点「下载报表」那一刻**就触发的，而点按钮发生在
    `detect_export_mode` 里 —— **早于**开始监听，于是下载被错过（实测：
    问题订单数据 20 天全程"等不到下载"，而手动挂监听能接到）。

    这个监听器从头挂着，走哪个通道都能兜住。
    """
    os.makedirs(staging_dir, exist_ok=True)
    recent = []

    def _on_download(d):
        try:
            name = d.suggested_filename or "download.csv"
            p = os.path.join(staging_dir, name)
            d.save_as(p)
            recent.append(p)
        except Exception:
            pass

    page.on("download", _on_download)
    return recent


# 各表用于判定归属的日期列（**不能靠文件名认表**）
DATE_COL = {
    "门店财务明细": "开始时间",
    "门店成交明细": "开始日期",
    "商品数据": "日期",
    "问题订单数据": "下单时间",
    "流量明细(新)": "日期",
    "流量渠道明细(新)": "日期",
    "评价数据": "评价提交日期",
    "售后订单数据": "下单时间",
}


# 各表的**特征列**（必须含 / 必须不含）—— 只靠日期列区分不开表：
# 「问题订单数据」与「售后订单数据」的日期列**都是「下单时间」**（实测踩过）。
SIGNATURE = {
    "门店财务明细":    (["开始时间", "收入"], []),
    "门店成交明细":    (["开始日期", "推单数"], []),
    "商品数据":       (["商品SKU码"], []),
    "问题订单数据":    (["商家服务问题"], []),          # 与售后的分水岭
    "流量明细(新)":    (["曝光人数"], ["渠道"]),        # 渠道表才有「渠道」列
    "流量渠道明细(新)":  (["渠道", "曝光人数"], []),
    "评价数据":       (["评价提交日期"], []),
    "售后订单数据":    (["展示订单号"], []),           # 与问题订单的分水岭
}


def read_raw_csv(path):
    """读原始 CSV → (header, rows)，按文本，自动判编码。读不出返回 (None, None)。"""
    try:
        raw = open(path, "rb").read()
    except OSError:
        return None, None
    text = None
    for enc in ("gbk", "utf-8-sig"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return None, None
    import csv as _csv
    rows = [r for r in _csv.reader(text.splitlines()) if any(c.strip() for c in r)]
    if not rows:
        return None, None
    return [c.strip() for c in rows[0]], rows[1:]


def header_matches_table(header, report):
    """表头是否属于该表（特征列全中、且不含排除列）。"""
    must, must_not = SIGNATURE.get(report, ([], []))
    if not must:
        return False
    h = set(header)
    return all(c in h for c in must) and not any(c in h for c in must_not)


def file_matches_table_date(path, report, day):
    """按**内容**判断这个文件是不是「该表该日」的：表头匹配该表，且行日期都等于 day。"""
    header, rows = read_raw_csv(path)
    if header is None or not header_matches_table(header, report):
        return False
    col = DATE_COL.get(report)
    if not col or col not in header:
        return False
    i = header.index(col)
    want = day.replace("-", "")
    seen = 0
    for r in rows:
        v = re.sub(r"\D", "", r[i] if i < len(r) else "")
        if not v:
            continue
        seen += 1
        if want not in (v[:8], v[-8:]):
            return False
    return seen > 0


def take_recent_download(recent, report, day, out_dir, since_ts=None):
    """从监听器收到的文件里挑出本任务那份；挑不到返回 None。

    🔴 **必须按内容判定，不能按文件名**：平台给「问题订单数据」的原始文件名是
    `…-体检问题订单下载-….csv`（不含表名），按表名找永远匹配不上（实测踩过）。
    """
    if not recent:
        return None
    for p in reversed(list(recent)):
        if not os.path.exists(p):
            continue
        if since_ts is not None and os.path.getmtime(p) < since_ts - 2:
            continue                                  # 上一轮残留，不认
        if not file_matches_table_date(p, report, day):
            continue
        try:
            final = S.sniff_and_save(p, out_dir, "%s_%s" % (report, day))
        except Exception:
            continue
        try:
            rows, cols = S.validate_file(final, day, day)
        except Exception:
            rows, cols = 0, 0
        return {"file": final, "rows": rows, "cols": cols, "mode": "download-event"}
    return None


def _export(page, fr, report, mode, timeout_s):
    """按指定通道触发导出并取回临时文件。

    ⚠️ 超时不能一刀切：
      · 门店财务明细出任务较慢 —— 60 秒曾导致整表全失败（实测），故普通表给 120 秒
      · **事件型表**（问题订单/评价/售后）当天无事件时平台不产生文件，
        等下去毫无意义 —— 只给 45 秒，把 "空白日" 的代价从 2 分钟压到 45 秒
    """
    t = min(timeout_s, 45 if report in S.EVENT_REPORTS else 120)
    if mode == "direct":
        return S.direct_download(page, fr, timeout_s=t)
    S.dismiss_modals(fr)
    S.goto_download_list(fr)
    return S.poll_and_download(page, fr, report, timeout_s=t)


def recover_page(page, wait_s=150):
    """重载报表页并等 iframe 回来 —— 连跑久了页面状态会退化，重载是最可靠的重置。"""
    try:
        page.reload(wait_until="domcontentloaded")
    except Exception:
        pass
    deadline = time.time() + wait_s
    while time.time() < deadline:
        time.sleep(5)
        try:
            S.get_frame(page, timeout_s=20)
            time.sleep(3)
            return True
        except Exception:
            continue
    return False


NO_DATA_LEDGER = "无数据台账.json"


def _append_no_data(out_dir, table, day, note=""):
    """事件型表当天无事件 → 平台返回「无数据/无有效数据」，**不产生文件**。

    必须记台账，否则下一轮的「缺口统计」会把这天当成缺数据反复重采，
    最终对账也会把它报成**假缺口**。事件型表 = 问题订单数据 / 评价数据 / 售后订单数据。
    """
    p = os.path.join(out_dir, NO_DATA_LEDGER)
    ledger = []
    if os.path.exists(p):
        try:
            ledger = json.loads(open(p, encoding="utf-8").read())
        except (ValueError, OSError):
            ledger = []
    if not any(e.get("table") == table and e.get("date") == day for e in ledger):
        ledger.append({"table": table, "date": day, "note": (note or "")[:120]})
    os.makedirs(out_dir, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fo:
        json.dump(ledger, fo, ensure_ascii=False, indent=1)


def load_no_data_ledger(out_dir):
    """读无数据台账 → {(表, 日期)}。"""
    p = os.path.join(out_dir, NO_DATA_LEDGER)
    if not os.path.exists(p):
        return set()
    try:
        return {(e["table"], e["date"]) for e in json.loads(open(p, encoding="utf-8").read())}
    except (ValueError, OSError, KeyError):
        return set()


def _dump_manifest(out_dir, manifest):
    """每条任务后都写一次清单，运行中途崩了也不丢进度。"""
    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(out_dir, "采集清单.json")
    with open(p, "w", encoding="utf-8") as fo:
        json.dump(manifest, fo, ensure_ascii=False, indent=1)


def _load_tasks(path):
    """读任务清单 JSON。Windows 上别的工具可能写成 GBK，两种都试。"""
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "gbk"):
        try:
            return json.loads(raw.decode(enc))
        except (UnicodeDecodeError, ValueError):
            continue
    raise SystemExit("任务清单读不出：%s（既不是 UTF-8 也不是 GBK）" % path)


def daterange(a, b):
    d0 = date(int(a[:4]), int(a[5:7]), int(a[8:10]))
    d1 = date(int(b[:4]), int(b[5:7]), int(b[8:10]))
    out = []
    while d0 <= d1:
        out.append(d0.isoformat())
        d0 += timedelta(days=1)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", help="单表；与 --all 二选一")
    ap.add_argument("--all", action="store_true", help="8 张表全采")
    ap.add_argument("--date", help="单日 YYYY-MM-DD")
    ap.add_argument("--from", dest="d_from", help="起始日（与 --to 同用）")
    ap.add_argument("--to", dest="d_to", help="结束日")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--tasks", help="任务清单 JSON：[{\"table\":..,\"date\":..}]，按此逐条采")
    ap.add_argument("--sleep", type=float, default=5.0, help="每次导出的间隔秒数（避风控）")
    ap.add_argument("--limit", type=int, default=0, help="最多采多少个「表×日」，0=不限")
    ap.add_argument("--debug-dates", action="store_true", help="打印日期设置每一步的现场")
    ap.add_argument("--reload-every", type=int, default=0,
                    help="每 N 个任务重载报表页；0=不重载（实测重载既慢又易把 iframe 打散，默认关）")
    args = ap.parse_args()

    global S
    import shangou_report_download as S  # noqa: E402

    if args.tasks:
        tasks = _load_tasks(args.tasks)
        unknown = sorted({t["table"] for t in tasks if t["table"] not in S.REPORTS})
        if unknown:
            raise SystemExit("任务清单里有不认识的表名：%s" % unknown)
    else:
        if args.date:
            days = [args.date]
        elif args.d_from and args.d_to:
            days = daterange(args.d_from, args.d_to)
        else:
            raise SystemExit("需要 --date，或 --from 与 --to，或 --tasks")
        reports = TABLES if args.all else ([args.report] if args.report else [])
        if not reports:
            raise SystemExit("需要 --report <表名> 或 --all")
        unknown = [r for r in reports if r not in S.REPORTS]
        if unknown:
            raise SystemExit("不认识的表名：%s\n可选：%s" % (unknown, list(S.REPORTS)))
        tasks = [{"table": r, "date": d} for d in days for r in reports]

    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    manifest = []
    try:
        browser = S.connect_browser(pw)
        page = S.find_page(browser)
        recent = make_download_sink(page, os.path.join(args.out, ".staging"))
        done = 0
        total = len(tasks)
        prev_failed = False
        for i, task in enumerate(tasks, 1):
            if args.limit and done >= args.limit:
                break
            rep, day = task["table"], task["date"]
            fresh = bool(args.reload_every) and (i - 1) % args.reload_every == 0
            try:
                r = collect_one(page, rep, day, args.out, args.timeout, fresh=fresh,
                                debug=args.debug_dates, recent=recent)
            except BaseException as e:       # 单个任务再离谱也不能带走整批
                if "[ANTI_BOT]" in str(e):
                    print("\n[风控] 登录态被打断，请人工重新登录后重跑。已完成 %d/%d。" % (done, total))
                    return 4
                note = "%s: %s" % (type(e).__name__, str(e)[:150])
                print("     %s → 原地重试一次" % note[:90], flush=True)
                r = {"file": None, "failed": True, "note": note}
                time.sleep(5)
                try:
                    r = collect_one(page, rep, day, args.out, args.timeout,
                                    debug=args.debug_dates, recent=recent)
                except BaseException as e2:
                    r = {"file": None, "failed": True,
                         "note": "重试仍失败 %s: %s" % (type(e2).__name__, str(e2)[:130])}
            r.update({"table": rep, "date": day})
            manifest.append(r)
            done += 1
            flag = "○" if r.get("no_data") else ("✗" if r.get("failed") else "✓")
            print("  [%d/%d] %s %s %s %s" % (i, total, flag, day, rep,
                                             r.get("note", "") or "行%s" % r.get("rows")),
                  flush=True)
            if r.get("no_data"):
                _append_no_data(args.out, rep, day, r.get("note", ""))
            _dump_manifest(args.out, manifest)     # 每条都落盘，崩了也不丢进度
            prev_failed = bool(r.get("failed"))
            if args.sleep:
                time.sleep(args.sleep)
        p = os.path.join(args.out, "采集清单.json")
        _dump_manifest(args.out, manifest)
        n_fail = sum(1 for m in manifest if m.get("failed"))
        n_no = sum(1 for m in manifest if m.get("no_data"))
        n_ok = len(manifest) - n_fail - n_no
        print("\n完成：成功 %d / 无数据 %d / 失败 %d ｜ 清单 %s" % (n_ok, n_no, n_fail, p))
        return 1 if n_fail else 0
    finally:
        pw.stop()


if __name__ == "__main__":
    sys.exit(main())
