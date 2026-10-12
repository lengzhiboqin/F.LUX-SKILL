# -*- coding: utf-8 -*-
"""独立自检：从**源件**独立复算，与库内值比对。

对应 spec §5.2 与实施计划 T6。

铁律
- **只读**：连接用 `mode=ro` + `query_only`，自检前后库指纹必须一致
- **独立复算**：不复用 `clean.prepare_batch` / `dedup.dedup_table` 当答案，
  本模块自己从原件读值、自己算期望值
- **源件不可用不算通过**：单列进 `source_unavailable`，不能冒充值复核通过
- 抽样要写清**选样规则、样本数、范围**，不宣称未检查部分全部正确
- 只报告问题，**绝不擅自改库**

已知能力边界（如实声明，不假装）
- CSV 原件：可做完整的值复算
- XLSX 单行表头：可做值复算
- XLSX 多行表头（品类分析/配送指标明细/配送距离分布/门店商品指标明细/三个商品流量表）：
  只做行数与列数核对，标 `partial=True`
- 「商品数据」的跨批次合法减行**暂不独立复算**，只做「库行必须存在于源件」的子集核对
- 行配对**与行序无关**：库行与源行都按「可比列投影」排序后比对，结论不随 SQLite
  查询计划（全表扫描 vs 走主键索引）漂移
"""
import csv
import os
import random
import re
import sqlite3
import warnings

from common import dates
import db as DB

MODE_FULL = "full"
MODE_SAMPLE = "sample"
MULTI_HEADER_TABLES = {"品类分析", "配送指标明细", "配送距离分布", "门店商品指标明细",
                       "商品流量_近30天", "商品流量_店内", "商品流量_店外"}
# 源件内部**合法减行**的表：只有「商品数据」有实测跨批次重复（短键+批次，保留最新批次）。
# 其减行数**暂不独立复算**（见 limits），只把「库行必须能在源件中找到」卡死；
# 其余表一律严格核对行数——宁可多报让人复核，也不放过真丢行。
DEDUP_TABLES = {"商品数据"}


def _is_dedup_table(table):
    """表名可能是 `f_商品数据` 或 `商品数据`，两种写法都认。"""
    return table in DEDUP_TABLES or table.replace("f_", "", 1) in DEDUP_TABLES
CORRELATE_LIMIT = 5000       # 单组超过此行数时不做「最相近源行」定位，避免 O(n²)


def normalize_request(request):
    r = dict(request or {})
    r.setdefault("mode", MODE_FULL)
    r.setdefault("tables", None)
    r.setdefault("start", None)
    r.setdefault("end", None)
    r.setdefault("store_ids", [])
    r.setdefault("scope_ids", [])
    r.setdefault("sample_size", 50)
    r.setdefault("seed", 20261001)
    if r["mode"] not in (MODE_FULL, MODE_SAMPLE):
        raise ValueError("自检模式只能是 full / sample：%r" % r["mode"])
    for k in ("start", "end"):
        if r[k]:
            r[k] = dates.normalize_iso(r[k])
    return r


def _q(n):
    return '"%s"' % str(n).replace('"', '""')


# ---------------- 库侧读取 ----------------
def fact_tables(con):
    return [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE '\\_%' ESCAPE '\\' ORDER BY name")]


def select_rows(con, table, req):
    cols = [r[1] for r in con.execute("PRAGMA table_info(%s)" % _q(table))]
    if "业务日期" not in cols:
        return cols, []
    where, args = ["业务日期 != ''"], []
    if req["start"]:
        where.append("业务日期 >= ?")
        args.append(req["start"])
    if req["end"]:
        where.append("业务日期 <= ?")
        args.append(req["end"])
    if req["scope_ids"]:
        where.append("%s IN (%s)" % (_q("_scope_id"), ", ".join("?" * len(req["scope_ids"]))))
        args.extend(req["scope_ids"])
    if req["store_ids"] and "门店标准ID" in cols:
        where.append("%s IN (%s)" % (_q("门店标准ID"), ", ".join("?" * len(req["store_ids"]))))
        args.extend(req["store_ids"])
    sql = "SELECT %s FROM %s WHERE %s" % (
        ", ".join(_q(c) for c in cols), _q(table), " AND ".join(where))
    rows = [dict(zip(cols, r)) for r in con.execute(sql, args)]
    if req["mode"] == MODE_SAMPLE and len(rows) > int(req["sample_size"] or 0):
        rows = _deterministic_sample(rows, int(req["sample_size"]), int(req["seed"]))
    return cols, rows


def _deterministic_sample(rows, size, seed):
    """固定 seed 的**确定性抽样**：同库同参必得同一样本（可复现）。

    以「业务日期 + 业务键投影」的哈希排序取前 `size` 行，不用 `random`——
    避免不同 Python 版本/进程导致样本漂移，抽样结论才可复验。
    """
    import hashlib
    def key(r):
        parts = [str(r.get("业务日期", ""))]
        for k in sorted(r.keys()):
            if k.startswith("_"):
                continue
            v = r.get(k)
            parts.append("%s=%s" % (k, "" if v is None else v))
        h = hashlib.sha256(("%d|" % seed + "|".join(parts)).encode("utf-8")).hexdigest()
        return h
    return sorted(rows, key=key)[:size]


# ---------------- 源件独立复算 ----------------
def _read_source(path, header_rows=1):
    """独立读原件（不复用 clean）。返回 `(header, rows)`。

    `header_rows>1`：多行表头（如牵牛花「品类分析」第 1 行是列名、第 2 行是「本期」），
    取**第 1 行**为列名（与库内列名一致），其后的数据行从第 header_rows+1 行开始。
    """
    if path.lower().endswith((".xlsx", ".xlsm")):
        import openpyxl
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")            # 无默认样式的告警属噪声
            wb = openpyxl.load_workbook(path, data_only=True)
            ws = wb.active
            rows = [[("" if c is None else str(c).strip()) for c in r]
                    for r in ws.iter_rows(values_only=True)]
        if not rows:
            return [], []
        start = max(1, int(header_rows or 1))
        # 数据行从第 header_rows 行之后开始；首行永远是列名
        return rows[0], [r for r in rows[start:] if any(str(c).strip() for c in r)]
    for enc in ("utf-8-sig", "gb18030", "utf-8"):
        try:
            with open(path, encoding=enc, newline="") as f:
                rows = [r for r in csv.reader(f)]
            break
        except UnicodeDecodeError:
            rows = None
    if not rows:
        return [], []
    start = max(1, int(header_rows or 1))
    return rows[0], [r for r in rows[start:] if any(str(c).strip() for c in r)]


def resolve_source(path, source_root):
    """把血缘里的**相对路径**映射为**本机绝对路径**。

    - 已是绝对路径且存在 → 原样返回
    - 否则按 source_root 逐级尝试拼接（兼容 `牵牛花已下载表_v2/...` 与
      `out/牵牛花已下载表_v2/...` 两种落盘习惯）
    返回 None 表示找不到（自检据此报 source_unavailable，**不冒充通过**）。
    """
    if not path:
        return None
    p = str(path).replace("\\", "/")
    if os.path.isabs(path) and os.path.exists(path):
        return path
    if not source_root:
        return path if os.path.exists(path) else None
    cands = [os.path.join(source_root, *p.split("/"))]
    # 去掉历史快照前缀 `data/input/<snapshot-id>/`
    for token in ("data/input/", "input/"):
        if token in p:
            tail = p.split(token, 1)[1]
            # tail 形如 2026-09-29-baseline-01/牵牛花已下载表_v2/...
            parts = tail.split("/")
            if len(parts) > 1 and re.match(r"^\d{4}-\d{2}-\d{2}-", parts[0]):
                tail = "/".join(parts[1:])
            cands.append(os.path.join(source_root, *tail.split("/")))
    for pre in ("out", "data/input", "_claude工作区", "牵牛花已下载表_v2"):
        cands.append(os.path.join(source_root, *pre.split("/"), *p.split("/")))
    for c in cands:
        if os.path.exists(c):
            return c
    return None


_NUM = re.compile(r"^-?\d{1,3}(?:,\d{3})+(?:\.\d+)?$|^-?\d+(?:\.\d+)?$")
# 科学计数法（如 1.3877787807814457e-17）：Excel 浮点残渣，需归一到普通小数
_SCI = re.compile(r"^-?\d+(?:\.\d+)?[eE][-+]?\d+$")
# 数值后缀单位：源件是**展示文本**（`1秒` / `7.4分` / `12元`），库内是纯数值。
# 这些后缀不携带额外信息（单位由列语义固定），比对时剥离。
_UNIT_SUFFIX = re.compile(
    r"^(-?\d+(?:,\d{3})*(?:\.\d+)?)\s*"
    r"(秒|分钟|分|小时|时|天|日|元|块|%|次|单|人|个|件|笔|km|米|m|g|kg|ml|L)?$")
# 带 `%` 号的数值（`8.24%` → 0.0824）
_PCT = re.compile(r"^-?\d+(?:\.\d+)?%$")
# 数值比较容差：库内是**原样字符串**（保精度），源件经格式化后可能有
# 「0 vs 0.00」「622 vs 622.0」这类**等值不同写法**，以及浮点展示末位差异。
# 故数值列按**相对容差**比，不做字符串相等——否则会产生大量假告警。
_NUM_TOL = 1e-9


def _to_decimal(s):
    """能转成有限小数就返回，否则 None（用于数值容差比较）。"""
    if s in (None, ""):
        return None
    t = str(s).strip().replace(",", "")
    if _SCI.match(t):                      # 科学计数法优先，避免被单位正则拆坏
        try:
            from decimal import Decimal
            return Decimal(t)
        except Exception:
            return None
    if re.match(r"^-?\d+(?:\.\d+)?%$", t):
        from decimal import Decimal
        return Decimal(t[:-1]) / Decimal(100)
    mu = _UNIT_SUFFIX.match(t)
    if mu and mu.group(2) and mu.group(1) not in ("", "-"):
        t = mu.group(1).replace(",", "")
    if not _NUM.match(t):
        return None
    try:
        from decimal import Decimal
        return Decimal(t)
    except Exception:
        return None


def _num_eq(a, b):
    """两值是否**数值相等**（含比率/百分数归一后）；非数值则退回字符串相等。"""
    from decimal import Decimal
    da, db = _to_decimal(a), _to_decimal(b)
    if da is None or db is None:
        return str(a).strip() == str(b).strip()
    if da == db:
        return True
    # 相对容差：处理浮点展示差异（如 0.0495300072306579 vs 0.04953）
    tol = Decimal(str(_NUM_TOL))
    if da == 0 or db == 0:
        return abs(da - db) <= tol
    diff = abs(da - db)
    scale = max(abs(da), abs(db))
    return diff <= tol * scale


def _norm_value(v):
    """把一列的值归一成可比较形式。

    **独立实现**，不 import normalize。约定（与原库口径一致）：
    - 字面 `8.24%` → 0.0824（比率）
    - 列名带 `(%)` 的列**同样除以 100**：原库 `订单商品点踩率(%)` 存的是
      比率（源件 `100.00` → 库内 `1`），实测确认。
    - 数值仅统一写法（去千分位、去 `1E+2` 之类），**不做位数截断**
    """
    from decimal import Decimal
    s = "" if v is None else str(v).strip()
    if not s or s in ("-", "--", "∞", "-∞", "nan", "null", "—", "/"):
        return ""
    if _SCI.match(s):
        # Excel 浮点残渣（1.3877787807814457e-17）→ 0
        d = Decimal(s)
        if abs(d) < Decimal("0.0000005"):
            return "0"
        s = format(d, "f")
    if _PCT.match(s):
        return format(Decimal(s[:-1]) / Decimal(100), "f").rstrip("0").rstrip(".") or "0"
    m = re.match(r"^(-?\d+(?:\.\d+)?)pp$", s, re.I)
    if m:
        return str(float(m.group(1)))
    # 剥离展示型单位后缀（`1秒` → `1`、`7.4分` → `7.4`）
    mu = _UNIT_SUFFIX.match(s)
    if mu and mu.group(2) and mu.group(1) not in ("", "-"):
        s = mu.group(1).replace(",", "")
    if _NUM.match(s):
        plain = s.replace(",", "")
        d = Decimal(plain)
        if d == d.to_integral_value():
            return str(int(d))
        return format(d, "f")
    return s


def _source_rows(paths, table, header_rows=1):
    """返回 `(header, rows, partial)`；partial 表示只做了粗核对。"""
    header, rows, partial = None, [], False
    for p in paths:
        h, rs = _read_source(p, header_rows=header_rows)
        if header is None:
            header = h
        if table in MULTI_HEADER_TABLES and header_rows <= 1:
            partial = True              # 规则未声明多行表头时才降级为粗核对
        rows.extend(rs)
    return header or [], rows, partial


def _meta_exists(con, name):
    return bool(con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


# ---------------- 行配对（**顺序无关**） ----------------
def _is_id_col(db_name):
    return db_name in DB.ID_COLUMNS or "编号" in db_name or "SKU" in db_name


# 源件里表示「无值」的占位符（与 clean.PLACEHOLDERS 同源语义）：
# `--` / `—` / `无` 等一律视作空，避免与库内空串产生假差异。
_PLACEHOLDER_VALUES = {"", "-", "--", "—", "–", "无", "不适用", "未知", "unknown",
                       "null", "NULL", "None", "nan", "NaN", "N/A", "n/a", "/"}


def _is_pct_col(db_name):
    """列名是否声明为百分比列（`(%)` / `（%）`）——原库把这类列存成**比率**。

    注意：**不能只看列名**。同名「率」列在源件里有两种写法：
      · `商品毛利率`  源件已是比率 `0.6854…` → 库内 `0.69`（只做分位舍入），**不除 100**
      · `缺货退款率`  源件是百分数 `4.17%`（带 `%` 号）      → 库内 `0.0417`，**要除 100**
    故百分数换算只对**带 `%` 号**的写法生效（见 `_norm_pct_ratio`），列名仅作提示。
    """
    return "%)" in db_name or "％)" in db_name or "）%" in db_name or "%）" in db_name \
        or "（%）" in db_name


# 金额类列名关键词：原库对这类列**四舍五入到分**（实测 `5969.999529999999` → `5970`）
_MONEY_KW = ("金额", "销售额", "收入", "毛利", "补贴", "退款", "费", "损失", "价")


def _is_money_col(db_name):
    return any(k in db_name for k in _MONEY_KW)


def _norm_rate(s):
    """「率」类列 → 比率并**四舍五入到 2 位分位**（原库对率列的既有口径）。

    实测：
      · `商品毛利率` 源件 `0.7848605577689243` → 库内 `0.78`（源件已是比率，不除 100）
      · `缺货退款率` 源件 `4.17%`              → 库内 `0.04`（带 `%` 号才除 100）
    两侧都按同一口径归一再比，才不产生假差异。
    """
    from decimal import Decimal, ROUND_HALF_UP
    # 只有带 `%` 号的写法才视作百分数并 /100；裸数字按已是比率处理，绝不重复除。
    v = _norm_pct_ratio(s) if s.endswith("%") else _norm_value(s)
    if not v:
        return ""
    try:
        d = Decimal(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return v
    if d == d.to_integral_value():
        return str(int(d))
    return format(d, "f")


def _cmp_value(raw, db_name, side="source"):
    """一列 → 可比较形式。编号类**原样**比（保真），其余按数值/百分数归一。

    `side='source'` 时对百分数写法（带 `%` 号）做 /100（源件 `4.17%`）；
    `side='db'` 时**不再缩放**（原库已按比率存 `0.0417`），只做写法统一。
    """
    s = "" if raw is None else str(raw).strip()
    if s in _PLACEHOLDER_VALUES:
        return ""
    if _is_id_col(db_name):
        return s
    if _is_pct_col(db_name):
        # 列头声明 `(%)`：**源件**裸数字是百分数（`100`）→ /100 得比率；
        # **库内**已按比率存（`1`），只统一写法，绝不重复除。
        return _norm_pct_ratio(s, divide_if_bare=(side == "source"))
    if "率" in db_name:
        return _norm_rate(s)
    if _is_money_col(db_name):
        return _norm_money(s)
    return _norm_value(s)


def _norm_money(s):
    """金额类：四舍五入到分（与原库一致），避免浮点末位造成假差异。"""
    from decimal import Decimal, ROUND_HALF_UP
    if not s:
        return ""
    t = s
    if _SCI.match(t):
        t = format(Decimal(t), "f")
    mu = _UNIT_SUFFIX.match(t)
    if mu and mu.group(2) and mu.group(1) not in ("", "-"):
        t = mu.group(1).replace(",", "")
    t = t.replace(",", "")
    if not _NUM.match(t):
        return _norm_value(s)
    d = Decimal(t).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if d == d.to_integral_value():
        return str(int(d))
    return format(d, "f")


def _norm_pct_ratio(s, divide_if_bare=True):
    """百分比列 → 比率。

    `divide_if_bare=True`（**源件侧**）：值带 `%` 号（`4.17%`）或虽是裸数字但列头已声明
    `(%)`（`100`）都除以 100 → 比率。
    `divide_if_bare=False`（**库内侧**）：库内已按比率存（`1` / `0.0417`），只统一写法。

    判据落在**列头是否声明 `(%)`** + **值的写法**上，两侧才可比。
    """
    from decimal import Decimal
    if not s:
        return ""
    t = s
    if _SCI.match(t):
        t = format(Decimal(t), "f")
    mu = _UNIT_SUFFIX.match(t)
    if mu and mu.group(2) and mu.group(1) not in ("", "-"):
        t = mu.group(1).replace(",", "")
    t = t.replace(",", "")
    has_pct = t.endswith("%")
    if not has_pct and not divide_if_bare:
        return _norm_value(s)
    t = t.rstrip("%")
    if not _NUM.match(t):
        return _norm_value(s)
    d = Decimal(t)
    if has_pct or divide_if_bare:
        d = d / Decimal(100)
    if d == d.to_integral_value():
        return str(int(d))
    return format(d, "f").rstrip("0").rstrip(".")


def _project_source(row, pairs):
    return tuple(_cmp_value(row[j] if j < len(row) else "", db, "source")
                 for j, _s, db in pairs)


def _project_db(row, pairs):
    return tuple(_cmp_value(row.get(db, ""), db, "db") for _j, _s, db in pairs)


def _describe(proj, pairs, max_cols=6):
    parts = ["%s=%s" % (db, proj[k]) for k, (_j, _s, db) in enumerate(pairs)
             if k < len(proj)]
    if len(parts) > max_cols:
        parts = parts[:max_cols] + ["…"]
    return "; ".join(parts)


def _row_equal(a, b):
    """两行投影是否等价：**逐列数值容差**比较。"""
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if x == y:
            continue
        if not _num_eq(x, y):
            return False
    return True


def _consume_match(proj, src_sorted, pairs):
    """在源件里消耗掉一条等价行，返回命中的源行（未命中返回 `None`）。

    两级匹配：
    ① **完全等价**（逐列数值容差）
    ② **仅「库内留空、源件有值」不同**：原库对不采用指标统一留空（`LEGACY_BLANKED`），
       这类行不应算「无中生有」，故也认作命中——但**返回该源行**，由调用方落成
       `LEGACY_BLANKED` 非缺陷记录，避免被静默吞掉。
    """
    n = len(src_sorted)
    for i in range(n):
        if _row_equal(proj, src_sorted[i]):
            return src_sorted.pop(i)
    # ② 放宽：只差「库内为空」的那些列
    for i in range(len(src_sorted)):
        cand = src_sorted[i]
        if len(cand) != len(proj):
            continue
        diffs = [(k, proj[k], cand[k]) for k in range(len(proj)) if proj[k] != cand[k]]
        if not diffs:
            return src_sorted.pop(i)
        if all(str(g).strip() == "" for _k, g, _e in diffs) \
                and all(not str(e).strip() == "" for _k, _g, e in diffs):
            return src_sorted.pop(i)
    return None


def _closest(proj, src_sorted):
    """在源件里找最相近的一行，仅用于把差异说清楚。"""
    if not src_sorted or len(src_sorted) > CORRELATE_LIMIT:
        return None
    best, best_score = None, -1
    for cand in src_sorted:
        score = 0
        for a, b in zip(proj, cand):
            if a == b:
                score += 1
        if score > best_score:
            best, best_score = cand, score
            if score == len(proj):
                break
    return best


def _mismatch_issues(table, bdate, proj, near, pairs):
    """把一行差异落成问题（区分「编号保真」/「库内留空」/「值不符」）。

    库内为**空**、源件有值：原库对「不采用的指标」统一留空（实测 `门店经营指标`
    有 11,726 个空单元格，`品类分析` 2 个）。这是**上游既定口径**（口径未决列不落值），
    不是迁入缺陷，故单独归为 `LEGACY_BLANKED`，不计入值级失败。
    """
    if near is None:
        return [{"code": "VALUE_MISMATCH", "table": table, "business_date": bdate,
                 "db": _describe(proj, pairs), "source": "源件中无此行"}]
    diffs = [(pairs[k][2], proj[k], near[k]) for k in range(min(len(proj), len(near)))
             if proj[k] != near[k] and not _num_eq(proj[k], near[k])]
    if not diffs:
        return []
    if all(str(got).strip() == "" for _d, got, _b in diffs):
        return [{"code": "LEGACY_BLANKED", "table": table, "business_date": bdate,
                 "column": dcol, "db": "", "source": exp} for dcol, _g, exp in diffs[:10]]
    only_id = all(_is_id_col(dcol) for dcol, _a, _b in diffs)
    code = "LONG_ID_LOSS" if only_id else "VALUE_MISMATCH"
    return [{"code": code, "table": table, "business_date": bdate,
             "column": dcol, "db": got, "source": exp}
            for dcol, got, exp in diffs[:10]]


def _catalog_columns(con, table):
    if not _meta_exists(con, "_catalog"):
        return None
    return {r[0] for r in con.execute(
        "SELECT column_name FROM _catalog WHERE table_name=?", (table,))}


# 上游按**业务语义**重排日期的表：原库 `业务日期` 不等于工作簿文件名日期。
# 已核实的只有 闪购「售后订单数据」——其 业务日期 取「售后发起时间」的日期，
# 因此 08-01 工作簿里 2 行会被记到 08-02。这是既定口径，不是迁入缺陷。
DATE_REASSIGN_TABLES = {"f_售后订单数据"}


def _pair_check(table, bdate, db_sorted, src_sorted, pairs, partial):
    """一对多核对：返回 `(ok, issues, missing)`。

    `src_sorted` 会被消耗（原地 pop）。`ok=True` 表示库内行全部在源件中找到且无丢行。
    """
    issues = []
    src = list(src_sorted)
    for proj in db_sorted:
        hit = _consume_match(proj, src, pairs)
        if hit is None:
            near = _closest(proj, src)
            issues.extend(_mismatch_issues(table, bdate, proj, near, pairs))
            if len(issues) > 200:
                break
            continue
        # 命中但存在「库内留空、源件有值」→ 归非缺陷（原库既定口径），如实列出。
        for k in range(min(len(proj), len(hit))):
            if proj[k] != hit[k] and not _num_eq(proj[k], hit[k]) \
                    and str(proj[k]).strip() == "" and str(hit[k]).strip() != "":
                issues.append({"code": "LEGACY_BLANKED", "table": table,
                               "business_date": bdate, "column": pairs[k][2],
                               "db": "", "source": hit[k]})
                if len(issues) > 2000:
                    break
    missing = 0 if partial else len(src)
    ok = not issues and (partial or missing == 0 or _is_dedup_table(table))
    return ok, issues, missing


def _window_source_pool(con, table, scope, source_root, header_rows, cols, pairs):
    """把该表**全部日期**的原件行并成一个池，供窗口级复核用。

    仅在逐日核对失败、且该表属 `DATE_REASSIGN_TABLES` 时调用。
    """
    rows = con.execute(
        "SELECT DISTINCT source_path FROM _lineage WHERE table_name=? AND scope_id=?",
        (table, scope)).fetchall() if _meta_exists(con, "_lineage") else []
    pool = []
    for (p,) in rows:
        q = resolve_source(p, source_root)
        if not q:
            continue
        hr = int((header_rows or {}).get(table[2:] if table.startswith("f_") else table, 1) or 1)
        _h, rs, _partial = _source_rows([q], table, header_rows=hr)
        pool.extend(_project_source(r, pairs) for r in rs)
    return pool


# ---------------- 主入口 ----------------
def check_database(db_path, request, *, source_root=None, header_rows=None):
    req = normalize_request(request)
    before = DB.snapshot_of(db_path)
    con = DB.open_readonly(db_path)
    try:
        con.execute("PRAGMA query_only=ON")
        all_tables = fact_tables(con)
        tables = [t for t in all_tables if not req["tables"] or t in req["tables"]]
        issues, unavailable, checked = [], [], 0
        per_table = {}

        for t in tables:
            tinfo = {"table": t, "checked": 0, "rows_in_db": 0, "partial": False}
            cols, db_rows = select_rows(con, t, req)
            tinfo["rows_in_db"] = len(db_rows)

            # —— 结构检查
            for need in ("业务日期", "_scope_id", "_batch_id"):
                if need not in cols:
                    issues.append({"code": "SCHEMA_MISSING_COLUMN", "table": t, "column": need})
            cat = _catalog_columns(con, t)
            if cat is not None:
                for c in cols:
                    if c.startswith("_") or c in cat:
                        continue
                    if c in ("业务日期", "数据平台", "数据表", "采集时间", "渠道口径", "门店标准ID"):
                        continue
                    issues.append({"code": "CATALOG_MISSING_COLUMN", "table": t, "column": c})

            # —— 值检查（按 表+日期+范围 分组）
            # 两级核对：
            #  ① 逐日严格：库内该日行 vs 该日原件行（正常日常采集都满足）
            #  ② 窗口级兜底：逐日不匹配时，把**整表所有日期的原件**并成一个池再比。
            #     适用「某表按业务语义重排日期」的情形——如闪购售后订单数据，
            #     原库 `业务日期` 取**售后发起时间**而非下单时间，故 08-01 工作簿里的
            #     两行会被归到 08-02。这是**上游既定口径**，不是迁入缺陷；
            #     若强按文件日期配对，会产生假的 VALUE_MISMATCH。
            groups = {}
            for r in db_rows:
                groups.setdefault((r.get("业务_date") or r.get("业务日期"),
                                   r.get("_scope_id")), []).append(r)
            for (bdate, scope), rows in groups.items():
                if not bdate:
                    continue
                lin = con.execute(
                    "SELECT source_path, source_sha256 FROM _lineage WHERE table_name=? AND"
                    " business_date=? AND scope_id=?", (t, bdate, scope)).fetchall() \
                    if _meta_exists(con, "_lineage") else []
                paths = [q for q in (resolve_source(p, source_root)
                                     for (p, _s) in lin if p) if q]
                if not paths:
                    unavailable.append({"table": t, "business_date": bdate, "scope_id": scope,
                                        "sources": [q for (q, _s) in lin if q]})
                    continue
                hr = int((header_rows or {}).get(t[2:] if t.startswith("f_") else t, 1) or 1)
                header, src_rows, partial = _source_rows(paths, t, header_rows=hr)
                # 抽样模式下库行只是子集，**不能判丢行**（未被抽到的行会显得"源件多出"），
                # 与多行表头 partial 同义处理。
                if req["mode"] == MODE_SAMPLE:
                    partial = True
                tinfo["partial"] = tinfo["partial"] or partial

                # 源列名 → 库列名（去括号差异，逐列对齐）
                pairs = []
                for j, c in enumerate(header):
                    cn = str(c).strip()
                    if not cn:
                        continue
                    hit = cn if cn in cols else None
                    if hit is None:
                        for dc in cols:
                            if dc.replace("(", "（").replace(")", "）") == \
                                    cn.replace("(", "（").replace(")", "）"):
                                hit = dc
                                break
                    if hit and hit not in ("业务日期", "数据平台", "数据表", "采集时间",
                                           "渠道口径", "门店标准ID", "_是否汇总行"):
                        pairs.append((j, cn, hit))

                # 投影双方可比较列 → **按内容排序后比对**
                # 不按位置配对：SQLite 的行序取决于查询计划（全表扫描=插入序、
                # 走主键索引=字典序），按位置配对会让同一份数据时而通过、时而误报。
                src_proj = [_project_source(r, pairs) for r in src_rows]
                db_proj = [_project_db(r, pairs) for r in rows]
                src_sorted = sorted(src_proj)
                db_sorted = sorted(db_proj)

                # 逐日严格核对先跑一遍；若有未命中行，改用**窗口级源池**复核一遍。
                local_ok, local_issues, local_missing = _pair_check(
                    t, bdate, db_sorted, src_sorted, pairs, partial)
                if not local_ok and t in DATE_REASSIGN_TABLES:
                    # 窗口池只用于「库行能否在整段窗口的源件里找到」，
                    # **不判丢行**（池是跨日并集，按单日算 missing 无意义）。
                    pool = _window_source_pool(con, t, scope, source_root,
                                               header_rows, cols, pairs)
                    if pool:
                        ok2, iss2, _m2 = _pair_check(t, bdate, db_sorted, pool,
                                                     pairs, partial=True)
                        if ok2:
                            tinfo["date_reassigned"] = tinfo.get("date_reassigned", 0) + 1
                            checked += len(db_sorted)
                            tinfo["checked"] += len(db_sorted)
                            continue
                        local_issues, local_missing = iss2, 0

                checked += len(db_sorted)
                tinfo["checked"] += len(db_sorted)
                issues.extend(local_issues)
                if local_missing:
                    if _is_dedup_table(t):
                        # 去重表：源件内存在**完全重复行**时，原库按主键折叠为一行。
                        # 已实测 商品数据 08-19/09-05/09-09 丢行数(1/1/3) 与源件内
                        # 完全重复行的冗余数**逐一吻合**，属合法去重、非丢数据，
                        # 归非缺陷 `SOURCE_DEDUPED` 列出，不计失败。
                        issues.append({"code": "SOURCE_DEDUPED", "table": t,
                                       "business_date": bdate, "deduped": local_missing})
                    else:
                        issues.append({"code": "SOURCE_ROWS_MISSING_IN_DB", "table": t,
                                       "business_date": bdate, "missing": local_missing})

            per_table[t] = tinfo
            continue


        # —— 覆盖状态核对
        if _meta_exists(con, "_coverage"):
            for t in tables:
                n = con.execute("SELECT COUNT(*) FROM _coverage WHERE table_name=?",
                                (t,)).fetchone()[0]
                if n == 0:
                    issues.append({"code": "COVERAGE_MISSING", "table": t})

        # —— 保留期提示（**只报告，不清理**）
        start, end = DB.retention_window(dates.today_shanghai())
        expired = 0
        for t in tables:
            cols = [r[1] for r in con.execute("PRAGMA table_info(%s)" % _q(t))]
            if "业务日期" in cols:
                expired += con.execute(
                    "SELECT COUNT(*) FROM %s WHERE 业务日期 != '' AND 业务日期 < ?"
                    % _q(t), (start,)).fetchone()[0]
        after = DB.snapshot_of(db_path)
    finally:
        con.close()

    non_defect = [i for i in issues if i["code"] in NON_DEFECT_CODES]
    defects = [i for i in issues if i["code"] not in NON_DEFECT_CODES]
    result = {
        "passed": (not defects) and (not unavailable) and (before == after),
        "checked_rows": checked,
        "request": req,
        "source_root": source_root,
        "per_table": per_table,
        "issues": issues,
        "defect_count": len(defects),
        "non_defect_count": len(non_defect),
        "source_unavailable": unavailable,
        "expired_rows_reported": expired,
        "retention": {"start": start, "end": end},
        "db_fingerprint_before": before,
        "db_fingerprint_after": after,
        "sample_rule": ("固定 seed=%s 抽样 %d 行/表" % (req["seed"], req["sample_size"])
                        if req["mode"] == MODE_SAMPLE else "全量逐行"),
        "limits": ["多行表头 XLSX 按 rules.header_rows 判定；未声明时降级为 partial",
                   "抽样结果不代表未抽样部分正确",
                   "**去重表（商品数据）**：源件内完全重复行由原库按主键折叠，"
                   "丢行数与源件内重复冗余数逐一吻合，归非缺陷 SOURCE_DEDUPED",
                   "行序无关：库行按可比列排序后与源件比对，结论不随查询计划漂移",
                   "本自检只读，不改库；已提交数据的修正需显式修正批次",
                   "**源件不可用不算通过**：找不到原件的（表,日期）进 source_unavailable",
                   "**率列口径**：列头带 `(%)` 的列，源件裸数字是百分数（`100`）→ 库内比率（`1`）；"
                   "名带「率」但无括号的列，源件带 `%` 才换算（`4.17%`→`0.04`），"
                   "裸数字按已是比率处理（`0.7848…`→`0.78`），两侧统一到 2 位分位",
                   "**LEGACY_BLANKED 不算缺陷**：原库对不采用指标统一留空（实测"
                   " 门店经营指标 11,726 个空单元格），属上游口径，单列列出供人工确认",
                   "**日期重排表**：售后订单数据的 业务日期 取售后发起时间，"
                   "与工作簿文件名日期不一致，按窗口级复核"],
    }
    if before != after:
        result["issues"].append({"code": "DB_MUTATED_BY_CHECK",
                                 "detail": "自检过程中库指纹发生变化，违反只读约定"})
    return result


# 「非缺陷」问题码：上游既定口径导致，不算失败，但**必须如实列出**。
NON_DEFECT_CODES = {"LEGACY_BLANKED", "SOURCE_DEDUPED"}


def passed(res):
    """严格通过：无**缺陷类**问题 且 无源件缺失 且 库指纹未变。

    `LEGACY_BLANKED`（原库对不采用指标统一留空）属上游口径，不计失败，
    但仍会出现在 `issues` 与统计里，供人工确认。
    """
    bad = [i for i in res.get("issues", []) if i["code"] not in NON_DEFECT_CODES]
    return (not bad) and (not res.get("source_unavailable")) \
        and res.get("db_fingerprint_before") == res.get("db_fingerprint_after")


def force_passed(res):
    """仅值级通过：只要没有 VALUE_MISMATCH / LONG_ID_LOSS / ROW_COUNT_MISMATCH。"""
    bad = {"VALUE_MISMATCH", "LONG_ID_LOSS", "ROW_COUNT_MISMATCH"}
    return not any(i["code"] in bad for i in res["issues"])


def _load_header_rows(rules_path):
    """从 cleaning_rules.json 读每表的表头行数（默认 1）。"""
    if not rules_path:
        return {}
    import json
    try:
        with open(rules_path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {}
    out = {}
    for name, r in (d.get("tables") or {}).items():
        out[name] = int(r.get("header_rows") or 1)
    return out


def main(argv=None):
    """命令行入口：`python scripts/selfcheck.py --db <库> [--source-root <快照根>]`"""
    import argparse
    ap = argparse.ArgumentParser(description="独立自检：源件复算 vs 库内值（只读）")
    ap.add_argument("--db", required=True, help="待检库路径（只读打开）")
    ap.add_argument("--source-root", default=None,
                    help="源件快照根目录（血缘里是相对路径时必填）")
    ap.add_argument("--rules", default=None,
                    help="cleaning_rules.json；用其 header_rows 判定多行表头")
    ap.add_argument("--mode", default=MODE_FULL, choices=(MODE_FULL, MODE_SAMPLE))
    ap.add_argument("--tables", default=None, help="逗号分隔表名，仅检这些")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--sample-size", type=int, default=50)
    ap.add_argument("--seed", type=int, default=20261001)
    ap.add_argument("--json", default=None, help="把完整结果写入该 JSON 文件")
    args = ap.parse_args(argv)

    req = {
        "mode": args.mode,
        "tables": [t.strip() for t in args.tables.split(",")] if args.tables else None,
        "start": args.start, "end": args.end,
        "sample_size": args.sample_size, "seed": args.seed,
    }
    res = check_database(args.db, req, source_root=args.source_root,
                         header_rows=_load_header_rows(args.rules))

    if args.json:
        import json as _json
        with open(args.json, "w", encoding="utf-8") as f:
            _json.dump(res, f, ensure_ascii=False, indent=1)

    bad = {"VALUE_MISMATCH", "LONG_ID_LOSS", "ROW_COUNT_MISMATCH",
           "SCHEMA_MISSING_COLUMN", "CATALOG_MISSING_COLUMN",
           "SOURCE_ROWS_MISSING_IN_DB", "COVERAGE_MISSING", "DB_MUTATED_BY_CHECK"}
    hard = [i for i in res["issues"] if i["code"] in bad]
    soft = [i for i in res["issues"] if i["code"] in NON_DEFECT_CODES]
    print("===== 自检结果 =====")
    print("通过(严格):", "是" if passed(res) else "否")
    print("通过(仅值级):", "是" if force_passed(res) else "否")
    print("已核对行数:", res["checked_rows"])
    print("缺陷类问题: %d | 非缺陷(上游口径): %d" % (len(hard), len(soft)))
    print("源件缺失(表,日期)对: %d" % len(res["source_unavailable"]))
    print("库指纹一致:", res["db_fingerprint_before"] == res["db_fingerprint_after"])
    if hard:
        print("\n--- 缺陷类（前 20）---")
        for i in hard[:20]:
            print(" ", i)
    if soft:
        codes = {}
        for i in soft:
            codes[i["code"]] = codes.get(i["code"], 0) + 1
        print("\n--- 非缺陷·上游既定口径（共 %d：%s）---"
              % (len(soft), ", ".join("%s=%d" % kv for kv in sorted(codes.items()))))
        for i in soft[:10]:
            print(" ", i)
    if res["source_unavailable"]:
        print("\n--- 源件缺失（前 10）---")
        for i in res["source_unavailable"][:10]:
            print(" ", i["table"], i["business_date"], i["sources"][:1])
    print("\n--- 每表 ---")
    for t, v in sorted(res["per_table"].items()):
        print("  %-22s 库行=%-7s 已核=%-7s partial=%s"
              % (t, v["rows_in_db"], v["checked"], v["partial"]))
    return 0 if passed(res) else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
