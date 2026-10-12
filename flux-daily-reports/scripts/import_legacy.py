# -*- coding: utf-8 -*-
"""历史库迁入：把「原精选库」（jingxuan.db / meituan_data.db）适配进本技能包的最终库。

为什么需要它
------------
原库（`f_*` 事实表 + catalog/coverage_table_date/lineage/build_manifest）与本技能包
最终库（`_catalog`/`_coverage`/`_lineage`/`_batch` + 来源列 + 库管列）**结构不同但业务同源**。
本模块把原库数据**转写**成符合 `db.apply_batch` 契约的 `PreparedBatch`，从而：
1. 复用既有单事务入库、整组替换、保留期、元数据写入逻辑（不另写一套写库代码）
2. 历史数据与本技能包自采数据**落在同一个库、同一套元数据里**

契约要点（与 db.py 对齐）
- `prepared["groups"]` 每项：table / columns / rows / business_key / scope_id / snapshot /
  status / source_files / business_date / window_start / window_end / load_to_db
- 库管列 `_行序号/_scope_id/_batch_id` 由 `db.write_group` 自动补，**本模块不手工写**
- 来源列（数据平台/数据表/采集时间/渠道口径）由本模块按原库信息补进 rows

用法
----
    python scripts/import_legacy.py --legacy <原库> --db <目标库> --rules references/cleaning_rules.json

只读保证：原库一律 `mode=ro` 打开，绝不写原库。
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from common import contracts as C
import db as DB

# 原库的库管列（迁入时丢弃，由新库重建）
LEGACY_MANAGE_COLS = {"_源行定位"}
# 新库来源列（迁移时补入）
SRC_COLS = ("数据平台", "数据表", "采集时间", "渠道口径")


def _read_rules(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _legacy_tables(con):
    """原库事实表名（f_ 前缀）。"""
    cur = con.execute("select name from sqlite_master where type='table'")
    return [r[0] for r in cur.fetchall() if r[0].startswith("f_")]


def _legacy_columns(con, table):
    c2 = con.cursor()
    try:
        return [c[1] for c in c2.execute('PRAGMA table_info("%s")' % table)]
    finally:
        c2.close()


def _legacy_dates(con, table, date_col="业务日期"):
    c2 = con.cursor()
    try:
        rows = c2.execute('SELECT DISTINCT "%s" FROM "%s"' % (date_col, table)).fetchall()
    finally:
        c2.close()
    return sorted({str(r[0]).strip() for r in rows if r[0] and str(r[0]).strip()})


def _sibling_dates(con, table, date_col="业务日期"):
    """本表无 业务日期 列时，借同源表推断日期集合。

    实例：`商品数据`/`评价数据` 原库用 下单时间/评价提交时间 定位，无 业务日期。
    但它们与 `问题订单数据`/`售后订单数据` 同属闪购订单流，日期集合一致。
    取法：优先 `问题订单数据`（行少、日期全），退回 `售后订单数据`。
    找不到就返回 [] —— 宁可留空，也不臆造日期。
    """
    for fallback in ("f_问题订单数据", "f_售后订单数据"):
        if _has_table(con, fallback):
            ds = _legacy_dates(con, fallback, date_col)
            if ds:
                return ds, fallback
    return [], None


def _has_table(con, name):
    c2 = con.cursor()
    try:
        return bool(c2.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())
    finally:
        c2.close()


def _files_for_any(f, by_date):
    """该原件是否已被某个业务日期认领（两种日期写法都试）。"""
    p = str(f.get("path") or "")
    for d in by_date:
        if d and (d in p or d.replace("-", "") in p):
            return True
    return False


def _resolve_local_path(legacy_path, src_path):
    """把原库里的**快照内相对路径**映射为**本机真实路径**。

    原库 `snapshot_file.路径` 形如
        data/input/2026-09-29-baseline-01/牵牛花已下载表_v2/门店流量排行/x.xlsx
    而同一快照的 `SQLite 冻结库` 记录指向 **out/db/*.db**。约定：
    原库文件位于 `<快照根>/out/db/`，故 `快照根 = dirname(dirname(dirname(db)))`。
    返回 (local_path, snapshot_root) 或 (None, None)。
    """
    con = sqlite3.connect("file:%s?mode=ro" % legacy_path.replace("\\", "/"), uri=True)
    try:
        if not _has_table(con, "snapshot_file"):
            return None, None
        anchor = None
        for path, role in con.execute('SELECT "路径","角色" FROM "snapshot_file"'):
            if role == "SQLite 冻结库" and path:
                anchor = str(path).replace("\\", "/")
                break
        if not anchor:
            return None, None
        # 快照根：从 db 文件往上 3 层（out/db/x.db → out → 快照根）
        db_dir = os.path.abspath(legacy_path)
        root = os.path.dirname(os.path.dirname(os.path.dirname(db_dir)))
        if not src_path:
            return None, root
        p = str(src_path).replace("\\", "/")
        if "/2026-09-29-baseline-01/" in p:
            tail = p.split("/2026-09-29-baseline-01/", 1)[1]
        else:
            tail = p.lstrip("/")
        return os.path.join(root, *tail.split("/")), root
    finally:
        con.close()


def _legacy_source_files(con, short_table, snapshot_root=None, snapshot_id=None,
                         dates_in=None):
    """从原库提取该表的原件路径与 SHA-256。

    原库有两处记录：
    - `lineage` 表：按 (精选表, 精选列) 记 来源文件 / 源文件SHA256（多为摘要文本）
    - `snapshot_file` 表：**逐份工作簿的真 SHA-256**（路径/角色/字节数/SHA256）

    合并策略：先取 lineage 的来源表与路径线索，再用 snapshot_file 补齐真 SHA。
    只有 64 位十六进制才视为有效 SHA；其余记为 raw_sha_note 供人工复核。
    未入库表（不在 snapshot_file 角色「精选来源」内）退回 **本机路径推断**，
    以便自检能真正找到原件；找不到文件则**不写血缘**，由自检如实报 unavailable。
    """
    out = []
    notes = []
    seen = set()

    def _add(path, sha, src_table=None, snapshot_id=None, note=None, role=None, size=None):
        key = (path, sha)
        if key in seen:
            return
        seen.add(key)
        rec = {"path": path, "sha256": sha, "source_table": src_table,
               "snapshot_id": snapshot_id, "role": role, "bytes": size}
        if sha:
            out.append(rec)
        else:
            rec["raw_sha_note"] = note
            notes.append(rec)

    def _valid_sha(v):
        s = str(v or "").strip()
        return s if len(s) == 64 and all(c in "0123456789abcdef" for c in s.lower()) else None

    # 1) lineage：给出来源表 + 路径线索
    if _has_table(con, "lineage"):
        c2 = con.cursor()
        try:
            cols = [c[1] for c in c2.execute('PRAGMA table_info("lineage")')]
            if "精选表" in cols:
                for r in c2.execute(
                        'SELECT "来源表","来源文件","源文件SHA256","源快照ID" FROM "lineage" '
                        'WHERE "精选表"=?', ("f_" + short_table,)):
                    src_table, path, sha, snap = r[0], r[1], r[2], r[3]
                    _add(path, _valid_sha(sha), src_table, snap,
                         note=None if _valid_sha(sha) else (str(sha) if sha else None))
        finally:
            c2.close()

    # 2) snapshot_file：逐份工作簿真 SHA（按来源表名筛选路径）。**优先且去重后即为权威**
    if _has_table(con, "snapshot_file"):
        c2 = con.cursor()
        try:
            cols = [c[1] for c in c2.execute('PRAGMA table_info("snapshot_file")')]
            need = {"路径", "SHA256"}
            if need <= set(cols):
                for r in c2.execute('SELECT "路径","角色","字节数","SHA256" FROM "snapshot_file"'):
                    path, role, size, sha = r[0], r[1], r[2], r[3]
                    if not path or ("/" + short_table + "/") not in str(path).replace("\\", "/"):
                        continue
                    _add(path, _valid_sha(sha), None, None, None, role, size)
        finally:
            c2.close()

    # 3) 兜底：lineage 只给了**占位串**（如「来源文件」）或干脆没有真 SHA 时，
    #    改用**本机原件实算 SHA**（仅对逐日工作簿表）。占位串不算有效路径。
    _PLACEHOLDER = {"来源文件", "—", "-", "", "无"}
    usable = [f for f in out if str(f.get("path") or "").strip() not in _PLACEHOLDER]
    if not usable:
        out = []                                  # 丢弃占位记录，改用本机原件实算
        # 先试牵牛花逐日工作簿，再试闪购逐日 CSV（两平台原件目录不同）
        plan = (_daily_workbook_plan(con, short_table, snapshot_root)
                or _shangou_csv_plan(short_table, snapshot_root))
        # 只保留**本表实际覆盖的日期**之原件，避免把快照目录里更早/更晚的历史件全挂上
        if plan and dates_in:
            keep_days = set()
            for d in dates_in:
                keep_days.add(d)
                keep_days.add(d.replace("-", ""))
            plan = [(lo, rel) for (lo, rel) in plan
                    if any(k in rel for k in keep_days)]
        for local, rel in plan:
            try:
                h = hashlib.sha256(open(local, "rb").read()).hexdigest()
            except OSError:
                continue
            _add(rel, h, None, snapshot_id, None, "本机原件实算", os.path.getsize(local))
    return out, notes


def _shangou_csv_plan(short_table, snapshot_root):
    """闪购（美团商家端）逐日 CSV 原件清单。

    目录约定：`_claude工作区/shangou_raw/<YYYY-MM-DD>/<表名>_<YYYY-MM-DD>.csv`。
    只收正式导出，忽略 `.staging/` 与体检类临时文件。
    """
    if not snapshot_root:
        return []
    base = os.path.join(snapshot_root, "_claude工作区", "shangou_raw")
    if not os.path.isdir(base):
        return []
    out = []
    for day in sorted(os.listdir(base)):
        d = os.path.join(base, day)
        if not os.path.isdir(d) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            continue
        f = os.path.join(d, "%s_%s.csv" % (short_table, day))
        if os.path.exists(f):
            out.append((f, "_claude工作区/shangou_raw/%s/%s_%s.csv"
                        % (day, short_table, day)))
    return out


def _daily_workbook_plan(con, short_table, snapshot_root):
    """逐日工作簿表按「本表涉及的日期集合」列出本机原件清单。

    仅适用于**每个业务日期一份工作簿**的表。返回 [(local_path, snapshot_rel_path)]。
    只收录文件名含 8 位日期的正式工作簿，剔除 `_废弃_*`、`*_ch200` 等实验/废件。
    """
    if not snapshot_root:
        return []
    # 该表在原库是否有业务日期列；无则借同源表推断（未入库表也可能有）
    ds = []
    if _has_table(con, "f_" + short_table):
        if "业务日期" in _legacy_columns(con, "f_" + short_table):
            ds = _legacy_dates(con, "f_" + short_table)
        if not ds:
            ds, _ = _sibling_dates(con, "f_" + short_table)
    rel_dir = "牵牛花已下载表_v2/" + short_table
    local_dir = os.path.join(snapshot_root, *rel_dir.split("/"))
    if not os.path.isdir(local_dir):
        return []
    out = []
    for f in sorted(os.listdir(local_dir)):
        if not f.lower().endswith((".xlsx", ".xlsm")):
            continue
        if f.startswith("_废弃") or "_ch" in f or f.startswith("~$"):
            continue
        m = re.search(r"(20\d{6})", f)
        if not m:
            continue
        if ds:
            want = {d.replace("-", "") for d in ds}
            if m.group(1) not in want:
                continue
        out.append((os.path.join(local_dir, f), rel_dir + "/" + f))
    return out


def import_stores_from_legacy(con, db_path, report):
    """把原库 `store_master`（55 行）迁入新库 `_store_master`。

    原库字段：门店标准ID/牵牛花名称/牵牛花别名/闪购商家ID/闪购名称/状态/覆盖范围/例外类别/例外原因/例外备注
    新库 `_store_master`：store_id / name / aliases / updated_at —— 结构更简；
    为此把原库的丰富信息序列化进 aliases 与 name，保留可用性。
    """
    if not _has_table(con, "store_master"):
        report.append({"stores": "skipped", "reason": "原库无 store_master 表"})
        return 0
    c2 = con.cursor()
    try:
        cols = [c[1] for c in c2.execute('PRAGMA table_info("store_master")')]
        rows = c2.execute("SELECT * FROM store_master").fetchall()
    finally:
        c2.close()

    out_con = sqlite3.connect(db_path, isolation_level=None)
    n = 0
    try:
        out_con.execute("BEGIN IMMEDIATE")
        for r in rows:
            rec = dict(zip(cols, r))
            sid = str(rec.get("门店标准ID") or "").strip()
            if not sid:
                continue
            name = (rec.get("牵牛花名称") or rec.get("闪购名称") or "").strip()
            aliases = []
            if rec.get("牵牛花别名"):
                aliases.append(str(rec["牵牛花别名"]))
            if rec.get("闪购名称") and rec.get("闪购名称") != name:
                aliases.append(str(rec["闪购名称"]))
            out_con.execute(
                "INSERT OR REPLACE INTO _store_master (store_id, name, aliases, updated_at) "
                "VALUES (?,?,?,?)",
                (sid, name, json.dumps(aliases, ensure_ascii=False),
                 datetime.datetime.now().isoformat(timespec="seconds")))
            n += 1
        out_con.execute("COMMIT")
    except Exception:
        out_con.execute("ROLLBACK")
        raise
    finally:
        out_con.close()
    report.append({"stores": "ok", "rows": n, "source": "store_master"})
    return n


def build_prepared(legacy_path, db_path, rules, *, batch_id=None, business_today=None,
                   tables=None, collect_time=None, platform_of=None,
                   snapshot_root=None, include_unloaded=False):
    """把原库整体转写为一个可直接 `db.apply_batch` 的 PreparedBatch。

    返回 (prepared, report)。`report` 记录每表迁入行数、日期范围、被丢弃的库管列。
    `include_unloaded=True` 时，规则里 `load_to_db=false` 的表也一并迁入
    （历史快照需要「全量可比」，故历史迁入默认开启；日常采集仍按规则排除）。
    """
    rule_tables = rules["tables"]
    con = sqlite3.connect("file:%s?mode=ro" % legacy_path.replace("\\", "/"), uri=True)
    prepared_groups = []
    report = []
    # 快照根目录：用于把原库相对路径映射为本机路径
    _, snap_root = _resolve_local_path(legacy_path, "")
    snapshot_root = snapshot_root or snap_root
    try:
        for tname in _legacy_tables(con):
            short = tname[2:]                       # 去掉 f_ 前缀
            rule = rule_tables.get(short)
            if not rule:
                report.append({"legacy_table": tname, "status": "skipped",
                               "reason": "未在 cleaning_rules 中登记"})
                continue
            if tables and short not in tables:
                continue
            if not rule.get("load_to_db", True) and not include_unloaded:
                report.append({"legacy_table": tname, "status": "skipped",
                               "reason": "规则声明不入库（include_unloaded=False）"})
                continue

            old_cols = _legacy_columns(con, tname)
            # 丢弃原库库管列；其余列原样保留（含已 rename 后的业务列名）
            keep = [c for c in old_cols if c not in LEGACY_MANAGE_COLS]

            # 补来源列（原库没有）
            platform = (platform_of or {}).get(short) or rule.get("platform") or ""
            channel = rule.get("channel_scope") or ""
            out_cols = list(keep) + [c for c in SRC_COLS if c not in keep]

            date_col = "业务日期" if "业务日期" in keep else None
            c2 = con.cursor()
            try:
                sel = ", ".join('"%s"' % c for c in keep)
                raw = c2.execute('SELECT %s FROM "%s"' % (sel, tname)).fetchall()
            finally:
                c2.close()

            rows = []
            for r in raw:
                vals = ["" if v is None else str(v) for v in r]
                rec = dict(zip(keep, vals))
                rec["数据平台"] = platform
                rec["数据表"] = short
                rec["采集时间"] = collect_time or ""
                rec["渠道口径"] = channel
                rows.append([rec.get(c, "") for c in out_cols])

            dates_in = _legacy_dates(con, tname) if date_col else []
            borrowed_from = None
            if not dates_in and "业务日期" in out_cols:
                # 无日期列的表：借同源表的日期集合补 业务日期（见 _sibling_dates）
                dates_in, borrowed_from = _sibling_dates(con, tname)
                if dates_in:
                    di = out_cols.index("业务日期")
                    n_sib = len(dates_in)
                    out_rows = []
                    for row in rows:
                        take = len(row) // n_sib if n_sib and len(row) % n_sib == 0 else 0
                        if take:
                            for k, d in enumerate(dates_in):
                                rr = list(row)
                                rr[di] = d
                                out_rows.append(rr)
                        else:
                            rr = list(row)
                            rr[di] = dates_in[-1]
                            out_rows.append(rr)
                    rows = out_rows
                    report.append({"legacy_table": tname, "status": "date_borrowed",
                                   "reason": "本表无业务日期列，按同源表 %s 的 %d 个日期"
                                             "补齐并重建行" % (borrowed_from, len(dates_in))})
            if not rows:
                report.append({"legacy_table": tname, "status": "empty", "rows": 0})
                continue

            bkey = rule.get("business_key") or []
            opt_keys = [k for k in (rule.get("optional_keys") or []) if k in out_cols]
            snap = bool(rule.get("snapshot"))
            keys_in = [b for b in bkey if b in out_cols]

            # 血缘：原库 lineage/snapshot_file 按表记原件路径与 SHA；
            # 未入库表退回「本机原件实算 SHA」。没有真 SHA 就**不写血缘**，由自检如实报 unavailable。
            src_files, src_notes = _legacy_source_files(
                con, short, snapshot_root=snapshot_root, snapshot_id=batch_id,
                dates_in=dates_in)


            if snap:
                # 滚动快照：整表替换，按窗口结束日一个 group
                prepared_groups.append({
                    "table": tname, "columns": out_cols, "rows": rows,
                    "business_key": keys_in, "optional_keys": opt_keys,
                    "scope_id": "all", "snapshot": True,
                    "status": C.S_VALIDATED, "load_to_db": True,
                    "source_files": src_files,
                    "business_date": dates_in[-1] if dates_in else "",
                    "window_start": dates_in[0] if dates_in else "",
                    "window_end": dates_in[-1] if dates_in else "",
                })
            else:
                # 普通表：**按业务日期拆组**，使 _coverage 与 _lineage 逐日可查。
                # 血缘按日期归属：原件名含 20260801 → 挂给 2026-08-01 那天的 group。
                # 认不出日期的原件挂到**首个** group（并在 report 注明），既不丢也不重复。
                di = out_cols.index("业务日期") if "业务日期" in out_cols else None
                by_date = {}
                for row in rows:
                    d = str(row[di]).strip() if di is not None else ""
                    by_date.setdefault(d, []).append(row)

                def _files_for(d):
                    # 同时接受 `20260801`（xlsx 命名）与 `2026-08-01`（CSV 目录命名）
                    forms = {d.replace("-", ""), d}
                    return [f for f in src_files
                            if any(k and k in str(f.get("path") or "") for k in forms)]

                first = sorted(by_date)[0] if by_date else None
                orphan = [f for f in src_files if not _files_for_any(f, by_date)]
                for pos, d in enumerate(sorted(by_date)):
                    fl = _files_for(d)
                    if pos == 0:
                        fl = fl + orphan          # 认不出日期的原件挂首个 group
                    prepared_groups.append({
                        "table": tname, "columns": out_cols, "rows": by_date[d],
                        "business_key": keys_in, "optional_keys": opt_keys,
                        "scope_id": "all", "snapshot": False,
                        "status": C.S_VALIDATED, "load_to_db": True,
                        "source_files": fl,
                        "business_date": d,
                        "window_start": "", "window_end": "",
                    })

            report.append({
                "legacy_table": tname, "status": "ok", "rows": len(rows),
                "groups": (1 if snap else len(set(
                    str(r[out_cols.index("业务日期")]).strip() for r in rows))) if rows else 0,
                "dates": len(dates_in),
                "date_from": dates_in[0] if dates_in else None,
                "date_to": dates_in[-1] if dates_in else None,
                "source_files": len(src_files),
                "source_notes": src_notes,
                "lineage_scope": ("表级（原库 lineage 按表记录，未逐日拆分）"
                                  if snap else "逐日（按原件名日期归属）"),
                "date_borrowed_from": borrowed_from,
                "dropped_manage_cols": sorted(LEGACY_MANAGE_COLS & set(old_cols)),
                "added_src_cols": [c for c in SRC_COLS if c not in keep],
            })
    finally:
        con.close()

    prepared = {
        "batch_id": batch_id or ("legacy-import-" + (business_today or "unknown")),
        "business_today": business_today or "2026-09-30",
        "rule_version": rules.get("rule_version", "rules-hist-v1"),
        "groups": prepared_groups,
    }
    return prepared, report


def import_legacy(legacy_path, db_path, rules_path, *, batch_id=None,
                  business_today=None, tables=None, collect_time=None, platform_of=None,
                  include_stores=True, include_unloaded=False, snapshot_root=None):
    rules = _read_rules(rules_path)
    prepared, report = build_prepared(
        legacy_path, db_path, rules, batch_id=batch_id, business_today=business_today,
        tables=tables, collect_time=collect_time, platform_of=platform_of,
        snapshot_root=snapshot_root, include_unloaded=include_unloaded)
    res = DB.apply_batch(db_path, prepared, prepared["business_today"])

    # 门店主档：非日期型，单独迁（不随事实表 group）
    if include_stores:
        con = sqlite3.connect("file:%s?mode=ro" % legacy_path.replace("\\", "/"), uri=True)
        try:
            import_stores_from_legacy(con, db_path, report)
        finally:
            con.close()
    return {"write": res, "report": report}


def main(argv=None):
    ap = argparse.ArgumentParser(description="历史精选库 → 本技能包最终库 迁入")
    ap.add_argument("--legacy", required=True, help="原库路径（只读打开）")
    ap.add_argument("--db", required=True, help="目标最终库路径")
    ap.add_argument("--rules", default=os.path.join(HERE, "..", "references", "cleaning_rules.json"))
    ap.add_argument("--batch-id", default=None)
    ap.add_argument("--business-today", default="2026-09-30")
    ap.add_argument("--collect-time", default=None)
    ap.add_argument("--tables", default=None, help="逗号分隔短表名，仅迁这些")
    ap.add_argument("--snapshot-root", default=None,
                    help="本机快照根目录（含 牵牛花已下载表_v2/）；缺省按原库路径自动推断")
    ap.add_argument("--include-unloaded", action="store_true",
                    help="把规则里 load_to_db=false 的表也迁入（历史全量用）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    tables = [t.strip() for t in args.tables.split(",")] if args.tables else None
    if args.dry_run:
        rules = _read_rules(args.rules)
        _, report = build_prepared(args.legacy, args.db, rules,
                                   batch_id=args.batch_id, business_today=args.business_today,
                                   tables=tables, collect_time=args.collect_time,
                                   snapshot_root=args.snapshot_root,
                                   include_unloaded=args.include_unloaded)
        print(json.dumps({"dry_run": True, "report": report}, ensure_ascii=False, indent=1))
        return 0

    out = import_legacy(args.legacy, args.db, args.rules, batch_id=args.batch_id,
                        business_today=args.business_today, tables=tables,
                        collect_time=args.collect_time,
                        snapshot_root=args.snapshot_root,
                        include_unloaded=args.include_unloaded)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
