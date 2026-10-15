# -*- coding: utf-8 -*-
"""日常入口的核心：七日缺口规划、批次身份、采集编排、原件验收。

对应 spec §4（S1-1 ~ S1-4）与实施计划 T1/T2/T3。

    from run_daily import plan_daily, run_collection, validate_raw

分工
- `plan_daily` / `plan_targeted` —— 纯函数，不碰网络；产出 `Plan`
- `run_collection` —— 逐个任务调用采集适配器，产出 `BatchManifest`
- `validate_raw` —— 原件验收（格式/工作表/完整有序表头/日期/大小/校验和）

原件验收是**全批次闸门**的一部分：任何一项不是 `validated/no_data`，整批不得进入清洗。
"""
import csv
import hashlib
import os

from common import contracts as C
from common import dates
from common import registry as REG

# ---------------- 台账 ----------------
def load_ledger(path):
    if not path or not os.path.exists(path):
        return {}
    return C.read_json(path)


def save_ledger(path, ledger):
    return C.atomic_write_json(path, ledger)


# ---------------- 路径约定 ----------------
def raw_dir(config, batch_id):
    return os.path.join(config["data_root"], "raw", batch_id)


def raw_path(config, batch_id, task, ext):
    """原件路径：**逐批留存**，不同批次/范围不互相覆盖。"""
    d = raw_dir(config, batch_id)
    os.makedirs(d, exist_ok=True)
    if task.get("window_end"):
        tag = task["window_end"]
    else:
        tag = task["business_date"]
    scope = "" if task["scope_id"] == "all" else "_" + task["scope_id"].replace(":", "-")
    return os.path.join(d, "%s_%s%s%s.%s" % (
        task["table"], dates.to_compact(tag), scope, "", ext))


# ---------------- 规划 ----------------
def _scope(scope):
    s = dict(scope or {"scope_id": "all"})
    s.setdefault("scope_id", "all")
    return s


WINDOW_DAYS = 7


def _window_of(reg_entry, t):
    """某表在业务日期 T 下的 `(窗口日期列表, 目标日)`。

    - 事件表（评价）：窗口 T-8~T-2，目标 T-2
    - 普通日表：窗口 T-7~T-1，目标 T-1
    - 滚动快照不走这里（由 `snapshot_window` 单独处理）
    """
    offset = reg_entry["default_offset_days"]
    return dates.window_dates(t, WINDOW_DAYS, offset), dates.shift_iso(t, -offset)


def plan_daily(t, reg, ledger, *, scope=None, force=False, batch_seq="",
               exclude=None):
    """规划一次日常运行。

    先算**近七日缺口**（按台账逐任务比对，不以最大日期推断完整），
    再叠加**目标日任务**；重叠只保留一份。台账已完成的**目标日**任务在
    `force=False` 时跳过；`force=True` 时强制重采（验收用的三轮真实采集）。

    `batch_seq`：同一天需要多次独立运行时给不同值，否则同一天同一模式是**同一批次身份**。
    `exclude`：表名集合，这些表本次不规划（如商品流量在09:30未就绪，剥离到14:05）。
    `collect_via=="standalone"` 的表由独立脚本采集，主流程一律不规划（见 table_registry）。
    """
    t = dates.normalize_iso(t)                     # 非法日期在此抛出
    sc = _scope(scope)
    ledger = ledger or {}
    sw = reg.get("snapshot_window", {"days": 30, "offset": 1})
    excluded = set(exclude or ())

    window_tasks, target_tasks = [], []
    for e in reg["tables"]:
        if e.get("collect_via") == "standalone":
            continue                               # 独立脚本采集，主流程不规划
        if e["table"] in excluded:
            continue
        if e["type"] == "snapshot":
            ws, we = dates.snapshot_window(t, sw.get("days", 30), sw.get("offset", 1))
            snap = C.make_task(e["platform"], e["table"], we,
                               window_start=ws, window_end=we, scope=sc)
            window_tasks.append(snap)
            target_tasks.append(snap)
            continue
        wdates, target = _window_of(e, t)
        for d in wdates:
            window_tasks.append(C.make_task(e["platform"], e["table"], d, scope=sc))
        target_tasks.append(C.make_task(e["platform"], e["table"], target, scope=sc))

    gap = [x for x in window_tasks if not C.ledger_is_complete(ledger, x["task_key"])]
    if force:
        tasks = target_tasks + gap
    else:
        fresh_targets = [x for x in target_tasks
                         if not C.ledger_is_complete(ledger, x["task_key"])]
        tasks = gap + fresh_targets

    # 补漏在前、目标日在后；重叠按 task_key 去重（同一任务不导出两次）
    plan = C.make_plan(t, "daily", C.dedup_by_key(tasks), C.dedup_by_key(gap),
                       batch_seq=batch_seq)
    C.validate_plan(plan)
    return plan


def plan_targeted(t, day_list, reg, *, scope=None, batch_seq=""):
    """定向重采：指定日期清单，**创建新批次**。

    只允许历史/目标营业日；未来日期拒绝。滚动快照若平台不支持回溯，
    调用方需按 `unsupported_snapshots` 记入限制，**不得伪造完成**。
    """
    t = dates.normalize_iso(t)
    sc = _scope(scope)
    days_n = [dates.normalize_iso(d) for d in day_list]
    future = [d for d in days_n if d > t]
    if future:
        raise ValueError("定向采集不允许未来营业日：%s" % future)
    if not days_n:
        raise ValueError("定向采集日期列表为空")

    tasks, unsupported = [], []
    for e in reg["tables"]:
        if e.get("collect_via") == "standalone":
            continue                               # 独立脚本采集，定向回采也不规划
        if e["type"] == "snapshot":
            # 快照不支持任意历史回溯：默认只接受「窗口结束日 = T-1」的目标快照
            if dates.shift_iso(t, -1) in days_n:
                ws, we = dates.snapshot_window(t, 30, 1)
                tasks.append(C.make_task(e["platform"], e["table"], we,
                                         window_start=ws, window_end=we, scope=sc))
            else:
                unsupported.append({"table": e["table"],
                                    "reason": "平台不支持指定历史日的滚动快照回溯"})
            continue
        for d in days_n:
            tasks.append(C.make_task(e["platform"], e["table"], d, scope=sc))

    plan = C.make_plan(t, "targeted", C.dedup_by_key(tasks), [], batch_seq=batch_seq)
    plan["unsupported_snapshots"] = unsupported
    C.validate_plan(plan)
    return plan


def check_gap(t, reg, ledger, *, scope=None):
    """只做缺口检查，不采集 → 摘要 dict（S1-1 的检查结果）。"""
    t = dates.normalize_iso(t)
    plan = plan_daily(t, reg, ledger, scope=scope)
    by_status = {}
    for x in plan["gap_tasks"]:
        st = C.ledger_status(ledger, x["task_key"]) or "not_collected"
        by_status[st] = by_status.get(st, 0) + 1
    return {
        "business_today": t,
        "planned_tasks": len(plan["tasks"]),
        "gap_total": len(plan["gap_tasks"]),
        "by_status": by_status,
        "gaps": [{"task_key": x["task_key"], "platform": x["platform"],
                  "table": x["table"], "business_date": x["business_date"],
                  "window_start": x["window_start"], "window_end": x["window_end"],
                  "scope_id": x["scope_id"],
                  "status": C.ledger_status(ledger, x["task_key"]) or "not_collected"}
                 for x in plan["gap_tasks"]],
    }


# ---------------- 原件结构检查 ----------------
def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_historical(path=None):
    """历史有序表头基线：`{表: {header_rows, width, sheets}}`。"""
    p = path or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "references", "historical_headers.json")
    if not os.path.exists(p):
        return {}
    out = {}
    for e in C.read_json(p):
        s = (e.get("samples") or [{}])[0]
        out[e["table"]] = {
            "header_rows": s.get("header_rows"),
            "width": s.get("width"),
            "sheets": s.get("sheets"),
            "header_row_count": e.get("header_row_count"),
        }
    return out


def inspect_file(path, table):
    """读文件结构 → `{sheets, header_rows, width, rows, reader}`。

    读不了时 `reader` 写 `None` 并给出 `problem`；**不抛异常**，由调用方判定状态。
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        for enc in ("utf-8-sig", "gb18030", "utf-8"):
            try:
                with open(path, encoding=enc, newline="") as f:
                    rows = list(csv.reader(f))
                break
            except UnicodeDecodeError:
                rows = None
        if not rows:
            return {"reader": None, "problem": "CSV 读取失败或为空"}
        return {"reader": "csv", "sheets": ["csv"], "width": len(rows[0]),
                "header_rows": [rows[0]], "rows": len(rows) - 1, "problem": None}
    if ext in (".xlsx", ".xlsm"):
        try:
            import openpyxl
        except ImportError:
            return {"reader": None, "problem": "缺 openpyxl，无法读 XLSX"}
        try:
            wb = openpyxl.load_workbook(path, data_only=True, read_only=False)
        except Exception as e:                       # noqa: BLE001 —— 坏文件要如实报，不能崩
            return {"reader": None, "problem": "XLSX 打开失败：%s" % e}
        ws = wb.active
        width = ws.max_column
        hdr = [["" if c.value is None else str(c.value).strip()
                for c in ws[1]]]
        return {"reader": "openpyxl", "sheets": wb.sheetnames, "width": width,
                "header_rows": hdr, "rows": max(ws.max_row - 1, 0), "problem": None}
    return {"reader": None, "problem": "不支持的扩展名：%s" % ext}


def _header_mismatch(expected_rows, got_rows):
    """完整**有序**表头比对 → 差异说明列表（空列表=一致）。"""
    if expected_rows is None:
        return []
    diffs = []
    if len(expected_rows) != len(got_rows):
        diffs.append("表头行数 %d != 期望 %d" % (len(got_rows), len(expected_rows)))
        return diffs
    for i, (exp, got) in enumerate(zip(expected_rows, got_rows)):
        if len(exp) != len(got):
            diffs.append("第 %d 行列数 %d != 期望 %d" % (i + 1, len(got), len(exp)))
            continue
        for j, (a, b) in enumerate(zip(exp, got)):
            if str(a).strip() != str(b).strip():
                diffs.append("第 %d 行第 %d 列 %r != 期望 %r" % (i + 1, j + 1, b, a))
    return diffs


def validate_raw(task, result, reg, hist=None, *, expected_date=None):
    """原件验收 → 新 TaskResult（不改输入）。

    规则（spec §3.2）
    - `no_data` 必须带**平台证据**，否则降为 `failed(NO_DATA_UNVERIFIED)`
    - 0 字节 / 读不了 / 表头任意位置不一致 → `failed(ZERO_BYTES/PARSE_FAILED/STRUCTURE_CHANGED)`
    - 结构变化一律 `blocked`，**不自动补列、不重排、不改名**
    - 文件名日期与任务日期不符 → `blocked(DATE_MISMATCH)`
    """
    out = dict(result)
    out.setdefault("error", None)

    if result["status"] == C.S_NO_DATA:
        ev = result.get("evidence") or {}
        if not (ev.get("platform_task_id") or ev.get("no_data_response")
                or ev.get("screenshot") or ev.get("export_task_id")):
            out["status"] = C.S_FAILED
            out["error"] = {"code": "NO_DATA_UNVERIFIED",
                            "detail": "无事件未附平台证据，不能算完成"}
        return out

    if result["status"] != C.S_DOWNLOADED:
        return out                                  # failed/blocked 原样透传

    files = result.get("files") or []
    if not files:
        out["status"] = C.S_FAILED
        out["error"] = {"code": "EMPTY_DOWNLOAD", "detail": "status=downloaded 但没有文件"}
        return out

    hist_entry = (hist or {}).get(task["table"], {})
    exp_rows = hist_entry.get("header_rows")
    exp_width = hist_entry.get("expected_cols") or hist_entry.get("width")
    want_date = expected_date or dates.to_compact(
        task.get("window_end") or task["business_date"])

    checked = list(out.get("evidence", {}).get("structure") or [])
    for f in files:
        path = f.get("path")
        if not path or not os.path.exists(path):
            out["status"] = C.S_FAILED
            out["error"] = {"code": "PARSE_FAILED", "detail": "文件不存在：%s" % path}
            return out
        real_bytes = os.path.getsize(path)
        if f.get("bytes") is not None and f["bytes"] != real_bytes:
            out["status"] = C.S_FAILED
            out["error"] = {"code": "PARSE_FAILED",
                            "detail": "字节数不符：报 %s，实际 %s" % (f["bytes"], real_bytes)}
            return out
        real_sha = sha256_of(path)
        if f.get("sha256") and f["sha256"] != real_sha:
            out["status"] = C.S_FAILED
            out["error"] = {"code": "PARSE_FAILED", "detail": "SHA-256 不符：%s" % path}
            return out
        f["bytes"], f["sha256"] = real_bytes, real_sha

        if real_bytes == 0:
            out["status"] = C.S_FAILED
            out["error"] = {"code": "ZERO_BYTES",
                            "detail": "0 字节原件不是无事件：%s" % os.path.basename(path)}
            return out

        info = inspect_file(path, task["table"])
        if info.get("problem"):
            out["status"] = C.S_FAILED
            out["error"] = {"code": "PARSE_FAILED", "detail": info["problem"]}
            return out

        # 牵牛花：业务日期只在**文件名**上（无日期列）
        if task["platform"] == "牵牛花":
            base = os.path.basename(path)
            if want_date not in base:
                out["status"] = C.S_BLOCKED
                out["error"] = {"code": "DATE_MISMATCH",
                                "detail": "文件名不含期望日期 %s：%s" % (want_date, base)}
                return out
            # 范围校验：按渠道拆采时文件名必须带 _ch<渠道>，否则就是拿错了口径
            if task["scope_id"].startswith("channel:"):
                ch = task["scope_id"].split(":", 1)[1]
                if "_ch%s" % ch not in base:
                    out["status"] = C.S_BLOCKED
                    out["error"] = {"code": "SCOPE_MISMATCH",
                                    "detail": "文件不是渠道 %s 的拆采结果：%s" % (ch, base)}
                    return out

        diffs = _header_mismatch(exp_rows, info.get("header_rows") or [])
        if diffs:
            out["status"] = C.S_BLOCKED
            out["error"] = {"code": "STRUCTURE_CHANGED",
                            "detail": "原始表头与基线不一致（未自动补列/重排）",
                            "differences": diffs[:20]}
            return out
        if exp_width and info.get("width") != exp_width:
            out["status"] = C.S_BLOCKED
            out["error"] = {"code": "STRUCTURE_CHANGED",
                            "detail": "列数 %s != 期望 %s" % (info.get("width"), exp_width)}
            return out
        # 只有表头、没有任何数据行：**不等于无事件**，须有平台证据才算完成
        if not info.get("rows"):
            out["status"] = C.S_BLOCKED
            out["error"] = {"code": "EMPTY_DOWNLOAD",
                            "detail": "只有表头、无数据行；需平台无事件证据才可记为完成"}
            return out

        checked.append({"file": os.path.basename(path), "sheets": info.get("sheets"),
                        "width": info.get("width"), "rows": info.get("rows"),
                        "reader": info.get("reader")})

    ev = dict(out.get("evidence") or {})
    ev["structure"] = checked
    ev["validated_at"] = dates.today_shanghai()
    ev["structure_baseline_date"] = want_date
    out["evidence"] = ev
    out["status"] = C.S_VALIDATED
    out["error"] = None
    return out


# ---------------- 采集编排 ----------------
def run_collection(plan, config, *, collector=None, hist=None, ledger=None):
    """按 `Plan` 逐任务采集并验收 → `BatchManifest`。

    `collector(task, config) -> TaskResult`：默认走 `adapters.collect_task`；
    离线自测可注入假采集器，**不因此放宽任何验收**。
    """
    C.validate_plan(plan)
    if collector is None:
        from adapters import collect_task as collector       # 延迟导入，避免拉起浏览器依赖
    results = []
    for task in plan["tasks"]:
        raw = collector(task, config)
        raw.setdefault("task_key", task["task_key"])
        raw.setdefault("platform", task["platform"])
        raw.setdefault("table", task["table"])
        raw.setdefault("business_date", task["business_date"])
        raw.setdefault("window_start", task.get("window_start"))
        raw.setdefault("window_end", task.get("window_end"))
        raw.setdefault("scope_id", task["scope_id"])
        raw.setdefault("contract_version", task["contract_version"])
        raw.setdefault("files", [])
        raw.setdefault("evidence", {})
        raw.setdefault("error", None)
        raw.setdefault("attempts", 1)
        raw.setdefault("collected_at", dates.today_shanghai())
        results.append(validate_raw(task, raw, reg=_registry_for(config), hist=hist))

    manifest = dict(plan)
    manifest.pop("unsupported_snapshots", None)
    manifest["results"] = results
    manifest["collection_status"] = C.collection_status_of(results)
    manifest["processing_status"] = "pending"
    manifest["rule_version"] = None
    manifest["committed_version"] = None
    C.validate_manifest(manifest)
    if ledger is not None:
        for r in results:
            C.ledger_update(ledger, r)
    return manifest


def _registry_for(config):
    reg = config.get("_registry")
    return reg if reg is not None else REG.load_registry(config.get("registry_path"))


def summarize_manifest(manifest):
    """一行话摘要，供日志与汇报复用。"""
    n = len(manifest["results"])
    ok = sum(1 for r in manifest["results"] if r["status"] in C.COMPLETE_STATUSES)
    nd = sum(1 for r in manifest["results"] if r["status"] == C.S_NO_DATA)
    bad = [r for r in manifest["results"] if r["status"] not in C.COMPLETE_STATUSES]
    return {
        "batch_id": manifest["batch_id"],
        "business_today": manifest["business_today"],
        "mode": manifest["mode"],
        "planned": n, "complete": ok, "no_data": nd,
        "blocked_or_failed": len(bad),
        "collection_status": manifest["collection_status"],
        "failures": [{"task_key": r["task_key"], "status": r["status"],
                      "error": r.get("error")} for r in bad],
    }
