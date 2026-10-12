# -*- coding: utf-8 -*-
"""日期契约：格式互转 + 严格校验 + 窗口规划。

保留原 `common/dates.py` 的四形态转换（紧凑 / 带横线 / 日期时间 / 范围），
**新增**严格日历校验与窗口计算；业务日期一律在项目内用带横线 `YYYY-MM-DD`。

修复记录（对原始脚本）
- 原 `parse_biz_date` 接受 `20260230` 这类**形似但不存在的日历日期**；
  本模块新增 `normalize_iso` / `is_valid_iso`，非法日期在**打开浏览器之前**抛错。

窗口（spec §3.1，全部含两端）
- 普通日表：T-7 ~ T-1，共 7 日；目标日 T-1
- 闪购评价：T-8 ~ T-2，共 7 日；目标日 T-2
- 滚动快照：T-30 ~ T-1，共 30 日；以 T-1 为窗口结束日
"""
import re
from datetime import date, datetime, timedelta, timezone

CN_TZ = timezone(timedelta(hours=8))          # Asia/Shanghai 无夏令时，固定 +08:00

_COMPACT = re.compile(r"^\d{8}$")
_DASHED = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_DATETIME = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[ T]")
_RANGE = re.compile(r"^(\d{8})\s*-\s*(\d{8})$")


# ---------------- 原函数（保留） ----------------
def to_compact(d):
    """`'2026-08-01'` -> `'20260801'`；已经是紧凑型则原样返回。"""
    s = str(d).strip()
    if _COMPACT.match(s):
        return s
    return s.replace("-", "")


def to_dashed(d):
    """`'20260801'` -> `'2026-08-01'`；已经是带横线则原样返回。"""
    s = str(d).strip()
    if _DASHED.match(s):
        return s
    return "%s-%s-%s" % (s[:4], s[4:6], s[6:8])


def parse_biz_date(v):
    """把任意形态的业务日期归一成紧凑型 `YYYYMMDD`；无法解析返回 `None`（不抛异常）。

    单元格里常有「汇总」「--」这类非日期值，故**不抛异常**。
    ⚠️ 本函数**不做日历合法性校验**（`20260230` 会通过）；要严格校验用 `normalize_iso`。
    """
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        if float(v) != int(v):
            return None
        s = "%08d" % int(v)
        return s if _COMPACT.match(s) else None
    s = str(v).strip()
    if not s:
        return None
    m = _RANGE.match(s)
    if m:
        return m.group(2)
    if _COMPACT.match(s):
        return s
    m = _DASHED.match(s) or _DATETIME.match(s)
    if m:
        return "%s%s%s" % (m.group(1), m.group(2), m.group(3))
    return None


def daterange(start, end):
    """闭区间日期列表，输入输出都是紧凑型 `YYYYMMDD`。"""
    s = date(int(start[:4]), int(start[4:6]), int(start[6:8]))
    e = date(int(end[:4]), int(end[4:6]), int(end[6:8]))
    if e < s:
        raise ValueError("结束日期 %s 早于开始日期 %s" % (end, start))
    out, cur = [], s
    while cur <= e:
        out.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    return out


# ---------------- 严格校验与窗口（新增） ----------------
def is_valid_iso(s):
    """是否为**真实存在的日历日期**（`YYYY-MM-DD`，含闰年大小月）。"""
    if not isinstance(s, str) or not _DASHED.match(s):
        return False
    try:
        date(int(s[0:4]), int(s[5:7]), int(s[8:10]))
    except ValueError:
        return False
    return True


def normalize_iso(v):
    """任意可识别形态 → `YYYY-MM-DD`；**非法或不存在的日期抛 `ValueError`**。

    这是采集/处理入口的唯一日期校验口：非法日期必须在打开浏览器之前被拒。
    """
    if not isinstance(v, str):
        raise ValueError("业务日期必须是字符串，收到 %r" % (v,))
    s = v.strip()
    if _DASHED.match(s):
        iso = s
    elif _COMPACT.match(s):
        iso = to_dashed(s)
    else:
        raise ValueError("无法识别的业务日期：%r" % (v,))
    if not is_valid_iso(iso):
        raise ValueError("不存在的日历日期：%s" % iso)
    return iso


def today_shanghai(now=None):
    """当前北京时间日期（`YYYY-MM-DD`）。跨午夜后才会变成新的一天。"""
    return (now or datetime.now(CN_TZ)).astimezone(CN_TZ).strftime("%Y-%m-%d")


def shift_iso(iso, days):
    """日期平移（天，可负）。"""
    d = date(int(iso[0:4]), int(iso[5:7]), int(iso[8:10]))
    return (d + timedelta(days=days)).strftime("%Y-%m-%d")


def window_dates(t, days, offset):
    """`(t-offset-days+1) .. (t-offset)` 闭区间列表，含两端共 `days` 天。

    - 普通日表 `window_dates(T, 7, 1)` → T-7 ~ T-1
    - 闪购评价 `window_dates(T, 7, 2)` → T-8 ~ T-2
    """
    if days < 1:
        raise ValueError("窗口天数必须 >= 1")
    out, cur = [], shift_iso(t, -(offset + days - 1))
    for _ in range(days):
        out.append(cur)
        cur = shift_iso(cur, 1)
    return out


def snapshot_window(t, days=30, offset=1):
    """滚动窗口 `(window_start, window_end)`，默认 T-30 ~ T-1。"""
    return shift_iso(t, -(offset + days - 1)), shift_iso(t, -offset)
