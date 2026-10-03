# -*- coding: utf-8 -*-
"""最终业务库：单事务入库、修订替换、365 日保留、一致性备份。

对应 spec §5.1/§5.3/§5.4 与实施计划 T5。

唯一事实源：一个 SQLite 库（**不再保留「中间库 → 精选库」两层**）。
库内对象
- 事实表：每个入库表一张（+ `_scope_id` 范围键 + `_行序号` 组内序号 + `_batch_id`）
- `_catalog`    指标目录（列名 → 单位/粒度/正式或待核来源/规则版本）
- `_store_master` 门店主档（**非日期型，不随保留期误删**）
- `_coverage`   日期/范围覆盖与状态（含无事件）
- `_lineage`    行级来源定位（原件路径 + SHA-256 + 批次）
- `_batch`      成功批次与规则版本（**提交是否发生的权威证据**）

三条不变量
1. 事实、目录、覆盖、血缘、批次**同一事务**写入，任一失败整批回滚
2. 修订**先删该业务键整组、再插新组**（不是新增一行）
3. 事实按**业务日期**保留 T-365 ~ T-1；滚动快照按**窗口结束日**判期限，不裁切快照内容
"""
import datetime
import os
import shutil
import sqlite3

from common import contracts as C
from common import dates

SEQ_COL = "_行序号"
SCOPE_COL = "_scope_id"
BATCH_COL = "_batch_id"
ID_COLUMNS = frozenset((
    "订单编号", "商品SKU码", "UPC码", "展示订单号", "SPU",
    "店铺ID", "商家ID", "门店ID", "门店id", "门店标准ID",
))
RETENTION_DAYS = 365


# ---------------- 建表 ----------------
def _q(name):
    return '"%s"' % name.replace('"', '""')


SUMMARY_COL = "_是否汇总行"


def fact_ddl(table, columns, business_key):
    defs = ["%s INTEGER NOT NULL" % _q(SEQ_COL)]
    for c in columns:
        defs.append("%s %s" % (_q(c), "INTEGER" if c == SUMMARY_COL else "TEXT"))
    defs.append("%s TEXT NOT NULL" % _q(SCOPE_COL))
    defs.append("%s TEXT NOT NULL" % _q(BATCH_COL))
    keys = [_q(c) for c in business_key] + [_q(SCOPE_COL), _q(SEQ_COL)]
    return 'CREATE TABLE IF NOT EXISTS %s (\n  %s,\n  PRIMARY KEY (%s)\n)' % (
        _q(table), ", ".join(defs), ", ".join(keys))


def ensure_meta_tables(con):
    con.execute("""CREATE TABLE IF NOT EXISTS _catalog (
        table_name TEXT NOT NULL, column_name TEXT NOT NULL, unit TEXT,
        grain TEXT, formal_source TEXT, status TEXT, rule_version TEXT,
        scope_id TEXT NOT NULL, PRIMARY KEY (table_name, column_name, scope_id))""")
    con.execute("""CREATE TABLE IF NOT EXISTS _store_master (
        store_id TEXT PRIMARY KEY, name TEXT, aliases TEXT, updated_at TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS _coverage (
        table_name TEXT NOT NULL, business_date TEXT NOT NULL, scope_id TEXT NOT NULL,
        window_start TEXT, window_end TEXT, status TEXT NOT NULL, rows INTEGER,
        batch_id TEXT, rule_version TEXT, PRIMARY KEY (table_name, business_date, scope_id))""")
    con.execute("""CREATE TABLE IF NOT EXISTS _lineage (
        table_name TEXT NOT NULL, business_date TEXT NOT NULL, scope_id TEXT NOT NULL,
        batch_id TEXT NOT NULL, source_path TEXT, source_sha256 TEXT, row_count INTEGER,
        PRIMARY KEY (table_name, business_date, scope_id, batch_id, source_sha256))""")
    con.execute("""CREATE TABLE IF NOT EXISTS _batch (
        batch_id TEXT PRIMARY KEY, business_today TEXT, rule_version TEXT,
        committed_at TEXT, inserted INTEGER, replaced INTEGER, expired INTEGER,
        status TEXT)""")


def db_columns(con, table):
    try:
        return [r[1] for r in con.execute('PRAGMA table_info(%s)' % _q(table))]
    except sqlite3.Error:
        return []


# ---------------- 校验 ----------------
def validate_group(g):
    """入库前的数据校验 → 问题列表（非空即整批失败）。

    键列空值规则
    - 默认：任一业务键列为空即报 EMPTY_KEY_VALUE
    - `optional_keys` 声明的键列（如「商品SKU码」——源端本就有合法空值）**允许留空**，
      同组多行由 `_行序号` 区分
    - 但**全部键列都空**的行一律报错（那是真丢键，不是合法空值）
    """
    issues = []
    cols = g["columns"]
    for k in g["business_key"]:
        if k not in cols:
            issues.append({"code": "MISSING_KEY_COLUMN", "table": g["table"], "column": k})
    optional = set(g.get("optional_keys") or ())
    if g["business_key"] and g["rows"]:
        idx = [cols.index(k) for k in g["business_key"] if k in cols]
        opt_idx = {cols.index(k) for k in optional if k in cols}
        for i, row in enumerate(g["rows"]):
            blanks = [j for j in idx if str(row[j]).strip() == ""]
            if not blanks:
                continue
            required_blank = [j for j in blanks if j not in opt_idx]
            if required_blank:
                issues.append({"code": "EMPTY_KEY_VALUE", "table": g["table"],
                               "row": i, "column": cols[required_blank[0]]})
            elif len(blanks) == len(idx):
                # 键列全空：即使是可选键也不接受
                issues.append({"code": "EMPTY_KEY_VALUE", "table": g["table"],
                               "row": i, "column": cols[blanks[0]],
                               "detail": "全部业务键为空，无法定位行"})
            if len(issues) > 20:
                break
    if g["status"] == C.S_VALIDATED and not g["rows"] and not g["snapshot"]:
        issues.append({"code": "EMPTY_VALIDATED_GROUP", "table": g["table"],
                       "detail": "验收通过却零行，拒绝写入"})
    for f in g["source_files"]:
        if not f.get("sha256"):
            issues.append({"code": "MISSING_SOURCE_SHA", "table": g["table"],
                           "path": f.get("path")})
    return issues


# ---------------- 写一组 ----------------
def _ensure_columns(con, g):
    """事实表列变化**必须停下确认**，不自动增补/重排。"""
    existing = db_columns(con, g["table"])
    if not existing:
        con.execute(fact_ddl(g["table"], g["columns"], g["business_key"]))
        return
    meta = {SEQ_COL, SCOPE_COL, BATCH_COL}
    miss = [c for c in g["columns"] if c not in existing and c not in meta]
    if miss:
        raise ValueError("%s 出现新列，需人工确认后再入库：%s" % (g["table"], miss))


def write_group(con, g, prepared, *, t):
    table = g["table"]
    _ensure_columns(con, g)

    if g["snapshot"]:
        return _write_snapshot(con, g, prepared)
    return _write_keyed(con, g, prepared)


def _write_keyed(con, g, prepared):
    """普通表：**按「表 + 业务日期集合 + 范围」整组替换**。

    为什么不是「按业务键删」：源端**整组减少或变为无事件**时，被删掉的那个键
    不会出现在新输入里，按业务键删就会留下幽灵行（实测会多计销量/金额）。
    一个 group 就是一次任务的原件结果，按它覆盖的业务日期集合整体替换才严密。
    """
    cols, rows = g["columns"], g["rows"]
    keys = g["business_key"]
    scope = g["scope_id"]

    date_idx = cols.index("业务日期") if "业务日期" in cols else None
    dates_in = sorted({str(r[date_idx]).strip() for r in rows
                       if date_idx is not None and str(r[date_idx]).strip()})
    if not dates_in:
        dates_in = [g["business_date"]]           # 空组（no_data）也必须能清掉旧组

    ph = ", ".join("?" * len(dates_in))
    cur = con.execute(
        "DELETE FROM %s WHERE %s = ? AND %s IN (%s)"
        % (_q(g["table"]), _q(SCOPE_COL), _q("业务日期"), ph),
        [scope] + dates_in)
    deleted = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    seen = {}
    for r in rows:
        k = tuple(str(r[cols.index(c)]).strip() for c in keys)
        seen[k] = seen.get(k, 0) + 1

    insert_cols = [SEQ_COL] + list(cols) + [SCOPE_COL, BATCH_COL]
    sql = "INSERT INTO %s (%s) VALUES (%s)" % (
        _q(g["table"]), ", ".join(_q(c) for c in insert_cols),
        ", ".join("?" * len(insert_cols)))
    seq = {}
    payload = []
    for r in rows:
        k = tuple(str(r[cols.index(c)]).strip() for c in keys)
        seq[k] = seq.get(k, 0) + 1
        payload.append([seq[k]] + ["" if v is None else str(v) for v in r]
                       + [scope, prepared["batch_id"]])
    if payload:
        con.executemany(sql, payload)
    return {"table": g["table"], "mode": "group_replace", "inserted": len(payload),
            "replaced_keys": len(seen), "deleted_rows": deleted,
            "covered_dates": dates_in}


def _write_snapshot(con, g, prepared):
    """滚动快照：**整表替换**，且只允许更完整/更新的快照。"""
    cols, rows = g["columns"], g["rows"]
    end = g["window_end"] or g["business_date"]
    cur = con.execute("SELECT DISTINCT %s FROM %s" % (_q("业务日期"), _q(g["table"]))) \
        if "业务日期" in db_columns(con, g["table"]) else []
    cur_days = {r[0] for r in cur} if cur else set()
    if len(cur_days) > 1:
        raise ValueError("快照表在库中出现多个窗口结束日：%s" % sorted(cur_days))
    if rows and cur_days and end < cur_days.pop():
        raise ValueError("快照 %s 比库内更旧，拒绝榜单倒退" % end)
    n = con.execute("SELECT COUNT(*) FROM %s" % _q(g["table"])).fetchone()[0]
    con.execute("DELETE FROM %s" % _q(g["table"]))
    insert_cols = [SEQ_COL] + list(cols) + [SCOPE_COL, BATCH_COL]
    sql = "INSERT INTO %s (%s) VALUES (%s)" % (
        _q(g["table"]), ", ".join(_q(c) for c in insert_cols), ", ".join("?" * len(insert_cols)))
    payload = [[i + 1] + ["" if v is None else str(v) for v in r]
               + [g["scope_id"], prepared["batch_id"]] for i, r in enumerate(rows)]
    if payload:
        con.executemany(sql, payload)
    return {"table": g["table"], "mode": "snapshot", "inserted": len(payload),
            "replaced_keys": 1 if n else 0, "deleted_rows": n}


def classify_column(col):
    """列 → 指标目录分类。**未决列一律 pending，不给正式来源。**"""
    pending = ("未决" in col) or ("待核" in col)
    unit = None
    for kw in ("金额", "销售额", "收入", "毛利", "补贴", "退款", "费", "损失"):
        if kw in col:
            unit = "元"
            break
    if unit is None and ("率" in col or "%" in col):
        unit = "比率"
    if unit is None and col.endswith("_pp"):
        unit = "百分点"
    return {"unit": unit,
            "status": "pending" if pending else "formal",
            "formal_source": None if pending else "原件直取"}


def write_catalog(con, g, rule_version):
    """指标目录：列名 → 单位/状态。未决列**不输出正式值**。"""
    for c in g["columns"]:
        info = classify_column(c)
        con.execute(
            "INSERT OR REPLACE INTO _catalog (table_name, column_name, unit, grain, "
            "formal_source, status, rule_version, scope_id) VALUES (?,?,?,?,?,?,?,?)",
            (g["table"], c, info["unit"], "day",
             info["formal_source"], info["status"], rule_version, g["scope_id"]))


def write_coverage(con, g, prepared):
    con.execute(
        "INSERT OR REPLACE INTO _coverage (table_name, business_date, scope_id, window_start,"
        " window_end, status, rows, batch_id, rule_version) VALUES (?,?,?,?,?,?,?,?,?)",
        (g["table"], g["business_date"], g["scope_id"], g["window_start"], g["window_end"],
         g["status"], len(g["rows"]), prepared["batch_id"], prepared["rule_version"]))


def write_lineage(con, g, prepared):
    for f in g["source_files"]:
        con.execute(
            "INSERT OR REPLACE INTO _lineage (table_name, business_date, scope_id, batch_id,"
            " source_path, source_sha256, row_count) VALUES (?,?,?,?,?,?,?)",
            (g["table"], g["business_date"], g["scope_id"], prepared["batch_id"],
             f.get("path"), f.get("sha256"), len(g["rows"])))


# ---------------- 保留期 ----------------
def retention_window(t):
    """保留 **T-365 ~ T-1**（含两端共 365 个自然日）。"""
    return dates.shift_iso(t, -RETENTION_DAYS), dates.shift_iso(t, -1)


def purge_expired(con, t, *, dry_run=False):
    """清理过期事实及其日期型覆盖/血缘；**门店主档、指标定义不动**。"""
    start, end = retention_window(t)
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE '\\_%' ESCAPE '\\'")]
    victim_rows, detail = 0, []
    for tb in tables:
        cols = db_columns(con, tb)
        if "业务日期" not in cols:
            continue
        n = con.execute("SELECT COUNT(*) FROM %s WHERE 业务日期 != '' AND 业务日期 < ?"
                        % _q(tb), (start,)).fetchone()[0]
        if n:
            detail.append({"table": tb, "rows": n})
            victim_rows += n
            if not dry_run:
                con.execute("DELETE FROM %s WHERE 业务日期 != '' AND 业务日期 < ?"
                            % _q(tb), (start,))
    existing = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for meta in ("_coverage", "_lineage"):
        if meta not in existing:
            continue
        n = con.execute("SELECT COUNT(*) FROM %s WHERE business_date < ?" % meta,
                        (start,)).fetchone()[0]
        if n:
            detail.append({"table": meta, "rows": n})
            victim_rows += n
            if not dry_run:
                con.execute("DELETE FROM %s WHERE business_date < ?" % meta, (start,))
    return {"retention_start": start, "retention_end": end,
            "expired_rows": victim_rows, "detail": detail}


# ---------------- 备份 ----------------
def backup_database(db_path, destination):
    """SQLite 在线备份 API → 一致性副本（**不是**文件拷贝）。"""
    os.makedirs(os.path.dirname(os.path.abspath(destination)), exist_ok=True)
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    from run_daily import sha256_of
    return {"path": os.path.abspath(destination),
            "bytes": os.path.getsize(destination),
            "sha256": sha256_of(destination)}


# ---------------- 主入口 ----------------
def apply_batch(db_path, prepared, t):
    """单事务入库 + 同事务过期清理。失败**整批回滚**，旧成功版本继续可查。"""
    t = dates.normalize_iso(t)
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)

    # 提交前校验（在开事务前先算，问题直接拒）
    issues = []
    for g in prepared["groups"]:
        if not g["load_to_db"]:
            continue
        issues.extend(validate_group(g))
    if issues:
        raise ValueError("入库前校验失败，整批拒绝：%s" % issues[:5])

    con = sqlite3.connect(db_path, isolation_level=None)
    inserted = replaced = expired = 0
    try:
        con.execute("BEGIN IMMEDIATE")
        ensure_meta_tables(con)
        for g in prepared["groups"]:
            if not g["load_to_db"]:
                write_coverage(con, g, prepared)      # 不入库也记覆盖与原因
                continue
            res = write_group(con, g, prepared, t=t)
            inserted += res["inserted"]
            replaced += res["replaced_keys"]
            write_catalog(con, g, prepared["rule_version"])
            write_coverage(con, g, prepared)
            write_lineage(con, g, prepared)

        purge = purge_expired(con, t)
        expired = purge["expired_rows"]
        con.execute(
            "INSERT OR REPLACE INTO _batch (batch_id, business_today, rule_version,"
            " committed_at, inserted, replaced, expired, status) VALUES (?,?,?,?,?,?,?,?)",
            (prepared["batch_id"], prepared["business_today"], prepared["rule_version"],
             dates.today_shanghai(), inserted, replaced, expired, "committed"))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()

    return {"batch_id": prepared["batch_id"],
            "committed_version": prepared["batch_id"],
            "inserted": inserted, "replaced": replaced, "expired": expired,
            "retention_start": purge["retention_start"],
            "retention_end": purge["retention_end"],
            "checks": {"pre_commit_issues": issues, "groups": len(prepared["groups"])}}


def maintain_retention(db_path, t, *, backup_dir, compact=False, dry_run=False):
    """独立保留期维护：先备份 + 截断预览，再按已批准规则清理；可选压缩。"""
    t = dates.normalize_iso(t)
    result = {"business_today": t, "backup": None, "preview": None,
              "deleted": 0, "compaction": {"executed": False, "space_released": 0}}
    if not dry_run:
        dest = os.path.join(backup_dir, "flux_%s.db" % t.replace("-", ""))
        result["backup"] = backup_database(db_path, dest)

    con = sqlite3.connect(db_path, isolation_level=None)
    try:
        con.execute("BEGIN IMMEDIATE")
        preview = purge_expired(con, t, dry_run=True)
        if dry_run:
            con.execute("ROLLBACK")
            result["preview"] = preview
            return result                               # 由 finally 统一关连接
        purged = purge_expired(con, t)
        con.execute("COMMIT")
        result["preview"] = preview
        result["deleted"] = purged["expired_rows"]
        result["retention_start"] = purged["retention_start"]
        result["retention_end"] = purged["retention_end"]

        if compact:
            before = con.execute("PRAGMA page_count").fetchone()[0] * \
                con.execute("PRAGMA page_size").fetchone()[0]
            free = con.execute("PRAGMA freelist_count").fetchone()[0]
            if free > 0:
                result["compaction"] = _compact(con, db_path, before)
            else:
                result["compaction"] = {"executed": False, "space_released": 0,
                                        "reason": "无可回收页"}
    except Exception:
        try:
            con.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        con.close()
    return result


def _compact(con, db_path, before_bytes):
    """VACUUM 回收空间；**失败单独报告，不伪报已释放**。"""
    try:
        con.execute("VACUUM")
        after = os.path.getsize(db_path)
        return {"executed": True, "space_released": max(before_bytes - after, 0)}
    except sqlite3.Error as e:
        return {"executed": False, "space_released": 0, "error": str(e)}


def open_readonly(db_path):
    """只读连接（自检/查询用）—— 绝不写库。"""
    return sqlite3.connect("file:%s?mode=ro" % db_path.replace("\\", "/"), uri=True)


def snapshot_of(db_path):
    """逻辑快照指纹：各表行数，用于证明「只读操作没有改库」。"""
    con = open_readonly(db_path)
    try:
        out = {}
        for (name,) in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            out[name] = con.execute("SELECT COUNT(*) FROM %s" % _q(name)).fetchone()[0]
        return out
    finally:
        con.close()


def copy_file(src, dst):
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    shutil.copy2(src, dst)
    return dst
