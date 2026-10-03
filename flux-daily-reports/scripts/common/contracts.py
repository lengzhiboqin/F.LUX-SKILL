# -*- coding: utf-8 -*-
"""统一交接契约：Task / Plan / TaskResult / BatchManifest / PreparedBatch /
WriteResult / CheckRequest / CheckResult / RecoveryDecision。

设计要点（spec §3、实施计划 §3）
- 用**普通 dict**，不引 dataclass，避免为接口重写历史脚本。
- 日期一律 `YYYY-MM-DD`；绝对数据路径只留在运行记录里，不进 `task_key`。
- `task_key` 只由**业务身份**决定（平台/表/日期或窗口/范围/契约版本），**不含采集时间**——
  否则同一业务任务每跑一次就是新键，台账永远算不出缺口。
- 本模块只做**校验与构造**，不碰浏览器、不碰数据库。
"""
import hashlib
import json
import os
import tempfile

from . import dates

CONTRACT_VERSION = "v1"

# TaskResult.status
S_DOWNLOADED = "downloaded"     # 已下载，尚未验收
S_VALIDATED = "validated"       # 原件验收通过（有数据）
S_NO_DATA = "no_data"           # 平台确认无事件（带证据）
S_BLOCKED = "blocked"           # 结构/范围/登录阻断，需人工或修复
S_FAILED = "failed"             # 采集失败

RESULT_STATUSES = frozenset((S_DOWNLOADED, S_VALIDATED, S_NO_DATA, S_BLOCKED, S_FAILED))
# 只有这两种状态算「任务合法完成」
COMPLETE_STATUSES = frozenset((S_VALIDATED, S_NO_DATA))

COLLECTION_STATUSES = frozenset(("pending", "partial", "complete", "blocked"))
PROCESSING_STATUSES = frozenset(("pending", "ready", "blocked", "processed",
                                 "processing", "failed"))

ERROR_CODES = frozenset(("LOGIN_REQUIRED", "BROWSER_UNAVAILABLE", "STRUCTURE_CHANGED",
                         "DATE_MISMATCH", "SCOPE_MISMATCH", "EMPTY_DOWNLOAD", "ZERO_BYTES",
                         "PARSE_FAILED", "TASK_NOT_FOUND", "TIMEOUT",
                         "PLATFORM_ERROR", "NO_DATA_UNVERIFIED",
                         # 平台/采集器**当前不具备**该能力（如指定历史日回采）。
                         # 用途：把「不支持」与「采失败」分开，禁止把能力缺口记成成功。
                         "UNSUPPORTED_DOWNLOAD"))

RECOVERY_ACTIONS = frozenset(("retry", "wait_user", "stop", "skip_committed"))


# ---------------- 通用 ----------------
def atomic_write_json(path, obj):
    """临时文件写完再替换 —— 崩溃不产生半截 JSON。"""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return path


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _sha1(s):
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


# ---------------- Task ----------------
def make_task(platform, table, business_date, *, window_start=None, window_end=None,
              scope=None, contract_version=CONTRACT_VERSION, structure_version=None):
    """构造一个 Task（dict）。

    - 日表/事件表：`business_date` 为业务日期，`window_*` 为 `None`
    - 滚动快照：`business_date` = 窗口结束日，`window_start/window_end` 必须给
    - `scope`：`{"scope_id": str, ...}`，默认 `{"scope_id": "all"}`
    """
    scope = dict(scope or {"scope_id": "all"})
    scope.setdefault("scope_id", "all")
    task = {
        "platform": platform,
        "table": table,
        "business_date": business_date,
        "window_start": window_start,
        "window_end": window_end,
        "scope_id": scope["scope_id"],
        "scope": scope,
        "contract_version": contract_version,
        "structure_version": structure_version,
    }
    task["task_key"] = task_key(task)
    return task


def task_key(task):
    """业务身份 → 稳定摘要。**不含采集时间、不含路径。**"""
    parts = [
        str(task.get("platform", "")),
        str(task.get("table", "")),
        str(task.get("business_date", "")),
        str(task.get("window_start") or ""),
        str(task.get("window_end") or ""),
        str(task.get("scope_id", "")),
        str(task.get("contract_version", "")),
    ]
    canonical = "|".join(parts)
    return "%s#%s" % (canonical, _sha1(canonical))


def task_identity(task):
    """用于比对「结果是否属于该任务」的身份字段（不含派生键）。"""
    return {k: task.get(k) for k in (
        "platform", "table", "business_date", "window_start", "window_end",
        "scope_id", "contract_version")}


# ---------------- Plan / BatchManifest ----------------
def new_batch_id(business_today, mode, *, seq=""):
    """批次 ID：日期 + 模式 + 短摘要。同一天同一模式的两次独立运行**必须给不同 seq**。"""
    raw = "%s|%s|%s" % (business_today, mode, seq)
    return "B%s-%s-%s" % (business_today.replace("-", ""), mode, _sha1(raw)[:8])


def make_plan(business_today, mode, tasks, gap_tasks, batch_seq=""):
    """批次 ID 由 `(business_today, mode, batch_seq)` 决定。

    - `batch_seq=""`（默认）→ **同一天同一模式得到同一批次身份**，恢复/重规划沿用旧批次
    - 需要「同一天多次独立运行」时（如验收三轮真实采集）显式传入不同 `batch_seq`
    """
    return {
        "batch_id": new_batch_id(business_today, mode, seq=batch_seq),
        "business_today": business_today,
        "mode": mode,
        "batch_seq": batch_seq,
        "tasks": list(tasks),
        "gap_tasks": list(gap_tasks),
    }


def dedup_by_key(tasks):
    """按 task_key 去重，保留首次出现顺序（目标日与补漏重叠时不导出两次）。"""
    seen, out = set(), []
    for t in tasks:
        k = t["task_key"]
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out


def validate_plan(plan):
    """Plan 自检；不合规抛 `ValueError`。"""
    for f in ("batch_id", "business_today", "mode", "tasks", "gap_tasks"):
        if f not in plan:
            raise ValueError("Plan 缺字段：%s" % f)
    if plan["mode"] not in ("daily", "targeted"):
        raise ValueError("未知 Plan.mode：%r" % plan["mode"])
    dates.normalize_iso(plan["business_today"])
    keys = [t["task_key"] for t in plan["tasks"]]
    if len(keys) != len(set(keys)):
        raise ValueError("Plan.tasks 存在重复 task_key")
    for t in plan["tasks"]:
        if t["task_key"] != task_key(t):
            raise ValueError("task_key 与业务身份不一致：%s" % t["task_key"])
    return True


def validate_manifest(manifest):
    """BatchManifest 自检：结果必须与计划任务**一一对应且身份相符**。"""
    for f in ("batch_id", "business_today", "mode", "tasks", "gap_tasks",
              "results", "collection_status", "processing_status"):
        if f not in manifest:
            raise ValueError("BatchManifest 缺字段：%s" % f)
    if manifest["collection_status"] not in COLLECTION_STATUSES:
        raise ValueError("未知 collection_status：%r" % manifest["collection_status"])
    if manifest["processing_status"] not in PROCESSING_STATUSES:
        raise ValueError("未知 processing_status：%r" % manifest["processing_status"])

    plan_keys = [t["task_key"] for t in manifest["tasks"]]
    res_keys = [r["task_key"] for r in manifest["results"]]
    if sorted(plan_keys) != sorted(res_keys):
        miss = sorted(set(plan_keys) - set(res_keys))
        extra = sorted(set(res_keys) - set(plan_keys))
        raise ValueError("结果与计划任务不匹配；缺 %s；多 %s" % (miss, extra))

    by_key = {t["task_key"]: t for t in manifest["tasks"]}
    for r in manifest["results"]:
        if r.get("status") not in RESULT_STATUSES:
            raise ValueError("未知 TaskResult.status：%r" % r.get("status"))
        ident = task_identity(r)
        if ident != task_identity(by_key[r["task_key"]]):
            raise ValueError("结果身份与任务不符：%s" % r["task_key"])
        if r["status"] in (S_VALIDATED, S_DOWNLOADED):
            if not r.get("files"):
                raise ValueError("已下载/已验收的结果必须带 files：%s" % r["task_key"])
            for f in r["files"]:
                for k in ("path", "bytes", "sha256"):
                    if k not in f:
                        raise ValueError("files 缺 %s：%s" % (k, r["task_key"]))
        if r["status"] == S_NO_DATA and not r.get("evidence"):
            raise ValueError("no_data 必须带平台证据：%s" % r["task_key"])
    return True


def collection_status_of(results):
    """由结果集合推导 collection_status。"""
    if not results:
        return "pending"
    st = {r["status"] for r in results}
    if st <= COMPLETE_STATUSES:
        return "complete"
    if st & {S_BLOCKED}:
        return "blocked"
    if st & {S_FAILED}:
        return "partial"
    return "partial"


def is_collection_complete(manifest):
    """全批次闸门：**全部**任务 `validated/no_data` 才准入清洗入库。"""
    if manifest["collection_status"] != "complete":
        return False
    return all(r["status"] in COMPLETE_STATUSES for r in manifest["results"])


# ---------------- 台账 ----------------
def ledger_status(ledger, key):
    """台账里某任务的状态；无记录返回 `None`。"""
    e = (ledger or {}).get(key)
    if not e:
        return None
    return e.get("status") if isinstance(e, dict) else e


def ledger_is_complete(ledger, key):
    return ledger_status(ledger, key) in COMPLETE_STATUSES


def ledger_update(ledger, result):
    """把一条 TaskResult 合并进台账（就地更新），返回台账。"""
    key = result["task_key"]
    ledger[key] = {
        "status": result["status"],
        "platform": result.get("platform"),
        "table": result.get("table"),
        "business_date": result.get("business_date"),
        "window_start": result.get("window_start"),
        "window_end": result.get("window_end"),
        "scope_id": result.get("scope_id"),
        "collected_at": result.get("collected_at"),
        "sha256": [f.get("sha256") for f in result.get("files") or []],
    }
    return ledger
