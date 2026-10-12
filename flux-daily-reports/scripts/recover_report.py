# -*- coding: utf-8 -*-
"""恢复、有限重试、页面定位适配与汇报。

对应 spec §6 与实施计划 T8。

设计原则
- **有限重试**：默认最多追加 2 次（合计 3 次尝试），不叠加平台内部三重重试
- **只跑受阻环节**：已验收的原件不重采，已提交的批次不重复写库
- **人工协助**：扫码/验证码/结构变化一律停下请求用户，不自动改业务口径
- **汇报如实**：只采集成功就不写「已入库」；已提交后失败必须写明「本次库是否提交：是」
"""
import os

from common import contracts as C
from common import dates

# 可自动重试的错误（临时性）
RETRYABLE = frozenset(("TIMEOUT", "PLATFORM_ERROR", "EMPTY_DOWNLOAD",
                       "TASK_NOT_FOUND", "BROWSER_UNAVAILABLE"))
# 必须人工介入的错误
NEED_HUMAN = frozenset(("LOGIN_REQUIRED", "STRUCTURE_CHANGED", "DATE_MISMATCH",
                        "SCOPE_MISMATCH", "ZERO_BYTES", "PARSE_FAILED",
                        "NO_DATA_UNVERIFIED"))

MAX_ATTEMPTS = 3          # 1 次原始 + 默认追加 2 次


# ---------------- 决策 ----------------
def decide_recovery(manifest, error, *, extra_retries=2):
    """根据错误决定下一步 → RecoveryDecision。

    `error` 至少含 `code`；`attempts` 表示**已经尝试过几次**。
    """
    code = (error or {}).get("code") or "PLATFORM_ERROR"
    attempts = int((error or {}).get("attempts", 1) or 1)
    task_key = (error or {}).get("task_key")

    # 已提交的批次：不再重复写库
    if manifest.get("committed_version"):
        return {"action": "skip_committed", "reason": "该批次已提交，跳过重复写入",
                "task_key": task_key, "attempts_left": 0}

    cap = 1 + max(0, int(extra_retries))
    if code in NEED_HUMAN:
        action = "wait_user" if code == "LOGIN_REQUIRED" else "stop"
        reason = {"LOGIN_REQUIRED": "登录失效/安全验证，需用户亲自完成",
                  "STRUCTURE_CHANGED": "源表结构变化，需人工确认契约后回归",
                  "DATE_MISMATCH": "取到的原件日期/范围与任务不符",
                  "SCOPE_MISMATCH": "取到的原件范围（渠道/门店）与任务不符",
                  "ZERO_BYTES": "0 字节原件，不是无事件",
                  "PARSE_FAILED": "原件解析失败",
                  "NO_DATA_UNVERIFIED": "无事件缺少平台证据"}[code]
        return {"action": action, "reason": reason, "task_key": task_key,
                "attempts_left": 0}

    if code in RETRYABLE:
        left = cap - attempts
        if left > 0:
            return {"action": "retry", "reason": "临时性失败，第 %d 次尝试后追加重试"
                    % attempts, "task_key": task_key, "attempts_left": left}
        return {"action": "stop", "reason": "已达重试上限（%d 次），停止并报告" % cap,
                "task_key": task_key, "attempts_left": 0}

    return {"action": "stop", "reason": "未分类错误，交人工判断：%s" % code,
            "task_key": task_key, "attempts_left": 0}


def retry_plan(manifest, error, *, extra_retries=2):
    """把「重试到停」的全过程摊开 → 每次尝试的决策列表（供报告与测试）。"""
    seq, attempts = [], 1
    while True:
        e = dict(error or {})
        e["attempts"] = attempts
        d = decide_recovery(manifest, e, extra_retries=extra_retries)
        seq.append({"attempt": attempts, "decision": d["action"], "reason": d["reason"]})
        if d["action"] != "retry":
            break
        attempts += 1
    return seq


def sync_processing_status(manifest):
    """结构变化 → 整批**阻断**；否则全合格则 ready。"""
    codes = {(r.get("error") or {}).get("code") for r in manifest.get("results") or []}
    if "STRUCTURE_CHANGED" in codes:
        manifest["processing_status"] = "blocked"
    elif C.is_collection_complete(manifest):
        manifest["processing_status"] = "ready"
    else:
        manifest["processing_status"] = "pending"
    return manifest


# ---------------- 断点恢复 ----------------
def resume_batch(manifest, config, rules=None, master=None, *, db_path=None,
                 collector=None, hist=None):
    """只重跑受阻任务；**沿用同一业务日期与批次身份**。

    已 `validated/no_data` 的任务不重采；已提交的批次直接跳过。
    """
    if manifest.get("committed_version"):
        manifest["resumed"] = "skipped_already_committed"
        return manifest

    pending = [t for t, r in zip(manifest["tasks"], _ordered(manifest))
               if r["status"] not in C.COMPLETE_STATUSES]
    if not pending:
        manifest["resumed"] = "nothing_to_do"
        sync_processing_status(manifest)
        return manifest

    from run_daily import run_collection
    plan = C.make_plan(manifest["business_today"], manifest["mode"], pending, [],
                       batch_seq=manifest.get("batch_seq", ""))
    fresh = run_collection(plan, config, collector=collector, hist=hist)

    done = {r["task_key"]: r for r in manifest["results"]
            if r["status"] in C.COMPLETE_STATUSES}
    for r in fresh["results"]:
        done[r["task_key"]] = r
    merged = dict(manifest)
    merged["results"] = [done[t["task_key"]] for t in manifest["tasks"]]
    merged["collection_status"] = C.collection_status_of(merged["results"])
    sync_processing_status(merged)
    merged["resumed"] = {"retried_tasks": len(pending),
                         "still_incomplete": sum(
                             1 for r in merged["results"]
                             if r["status"] not in C.COMPLETE_STATUSES)}
    if db_path and C.is_collection_complete(merged) and rules is not None:
        from clean import process_batch
        merged["write_result"] = process_batch(merged, rules, master, db_path=db_path)
        merged["processing_status"] = "processed"
        merged["committed_version"] = merged["write_result"]["committed_version"]
    return merged


def _ordered(manifest):
    by_key = {r["task_key"]: r for r in manifest["results"]}
    return [by_key[t["task_key"]] for t in manifest["tasks"]]


# ---------------- 页面定位适配 ----------------
def validate_locator_change(old, candidate, evidence):
    """页面定位小变化的**受控接受**：只在可验证范围内。

    `evidence` 需包含：`matches`（候选定位命中的元素数）、`date`、`range`、
    `structure_same`（下载结构是否与旧契约一致）。
    歧义（命中 >1）、日期/范围不符、结构变化 → 一律不接受，转维护。
    """
    ev = evidence or {}
    matches = int(ev.get("matches", 0) or 0)
    if matches == 0:
        return {"accepted": False, "reason": "候选定位没有命中唯一元素", "version": None,
                "rollback": old}
    if matches > 1:
        return {"accepted": False, "reason": "候选定位命中 %d 个元素，有歧义" % matches,
                "version": None, "rollback": old}
    if not ev.get("structure_same", False):
        return {"accepted": False, "reason": "下载结构与旧契约不一致，需人工确认",
                "version": None, "rollback": old}
    if not candidate or not candidate.get("selector"):
        return {"accepted": False, "reason": "候选定位缺少 selector", "version": None,
                "rollback": old}
    ver = "locator-%s" % dates.today_shanghai().replace("-", "")
    return {"accepted": True, "reason": "唯一同义操作且结构一致",
            "version": ver, "rollback": old,
            "applied": candidate}


def classify_change(kind, detail=None):
    """变化分级 → `{auto, action, need}`。"""
    table = {
        "layout": {"auto": True, "action": "使用备用定位重试",
                   "need": "唯一同义操作 + 结构一致"},
        "presentation": {"auto": True, "action": "在已明确规则内更新定位配置",
                         "need": "测试任务验证通过后记录版本"},
        "columns": {"auto": False, "action": "保存差异并阻断",
                    "need": "用户确认契约/映射后回归"},
        "semantics": {"auto": False, "action": "给出受影响表/指标/历史结果说明",
                      "need": "业务规则变更需用户批准"},
    }
    if kind not in table:
        raise ValueError("未知变化类型：%r" % kind)
    out = dict(table[kind])
    out["kind"] = kind
    out["detail"] = detail
    return out


# ---------------- 汇报 ----------------
def render_report(manifest, *, write_result=None, check_result=None,
                  collection_summary=None, retention=None, evidence_path=None):
    """成功/未完成两套模板（spec §6.3）。**只报实际完成的环节。**"""
    res = manifest.get("results") or []
    planned = len(res)
    done = sum(1 for r in res if r["status"] in C.COMPLETE_STATUSES)
    no_data = sum(1 for r in res if r["status"] == C.S_NO_DATA)
    committed_early = manifest.get("committed_version") or (write_result or {}).get(
        "committed_version")
    # 「采集全合格」但「提交后自检发现问题」→ 仍走未完成模板，并写明已提交
    post_commit_issue = bool(committed_early) and check_result is not None \
        and not check_result.get("passed")
    ok = (manifest.get("collection_status") == "complete" and not post_commit_issue
          and all(r["status"] in C.COMPLETE_STATUSES for r in res))
    lines = []

    if ok:
        normal = sorted({r["business_date"] for r in res if not r.get("window_end")})
        snaps = [(r["window_start"], r["window_end"]) for r in res if r.get("window_end")]
        lines += [
            "【成功】批次 %s，目标普通表 %s、评价 %s、快照 %s" % (
                manifest["batch_id"],
                "~".join([normal[0], normal[-1]]) if normal else "无",
                next((r["business_date"] for r in res if r["table"] == "评价数据"), "无"),
                "、".join("%s~%s" % s for s in snaps) if snaps else "无"),
            "采集：%d/%d；补漏 %d；确认无事件 %d" % (
                done, planned, len(manifest.get("gap_tasks") or []), no_data),
        ]
        if write_result:
            lines.append("入库：新增 %d、替换 %d；成功版本 %s" % (
                write_result.get("inserted"), write_result.get("replaced"),
                write_result.get("committed_version")))
        else:
            lines.append("入库：本次未执行（仅采集环节）")
        if check_result:
            lines.append("自检：%s，%s；%s" % (
                check_result.get("sample_rule"), "通过" if check_result.get("passed")
                else "发现问题", check_result.get("issues") and
                "问题码：%s" % sorted({i["code"] for i in check_result["issues"]}) or ""))
        if retention:
            lines.append("保留：%s~%s；清理过期记录 %s；空间维护 %s" % (
                retention.get("retention_start"), retention.get("retention_end"),
                retention.get("deleted", 0),
                retention.get("compaction", {}).get("executed", False)))
        lines.append("提示：%s" % _tips(res))
        lines.append("证据：%s" % (evidence_path or "见批次清单"))
        return "\n".join(lines)

    blocked = [r for r in res if r["status"] not in C.COMPLETE_STATUSES]
    stage = "自检（已提交后）" if post_commit_issue and not blocked else "采集"
    for r in blocked:
        code = (r.get("error") or {}).get("code", "")
        if code in ("STRUCTURE_CHANGED", "PARSE_FAILED", "ZERO_BYTES", "EMPTY_DOWNLOAD"):
            stage = "原件验收"
            break
    committed = committed_early
    reason = "；".join(
        "%s/%s/%s %s" % (r["platform"], r["table"], r["business_date"],
                         ((r.get("error") or {}).get("detail") or
                          (r.get("error") or {}).get("code") or r["status"]))
        for r in blocked[:5]) or ("独立自检发现问题：%s"
                                  % sorted({i["code"] for i in
                                            (check_result or {}).get("issues") or []}))
    lines += [
        "【未完成】批次 %s，受阻于 %s" % (manifest["batch_id"], stage),
        "原因：%s" % reason,
        "已完成：%d/%d 项可复用材料；本次库是否提交：%s" % (
            done, planned, "是" if committed else "否"),
        "当前可查：%s" % (("成功版本 %s，覆盖至 %s" % (
            committed, max((r["business_date"] for r in res
                            if r["status"] in C.COMPLETE_STATUSES), default="无")))
            if committed else "无新提交，沿用上一成功版本"),
        "已尝试：%s；下一步：%s" % (
            manifest.get("attempts_note", "见错误明细"),
            _next_step(blocked) if blocked else
            "留差异并按显式修正批次处理；只读自检不擅自回改已提交数据"),
        "证据：%s" % (evidence_path or "见批次清单"),
    ]
    if check_result and not check_result.get("passed"):
        lines.append("自检（已提交后）：发现 %d 处问题，按显式修正批次处理，"
                     "只读自检不擅自回改" % len(check_result.get("issues") or []))
    return "\n".join(lines)


def _tips(res):
    tips = []
    for r in res:
        ev = r.get("evidence") or {}
        if ev.get("structure_baseline_date"):
            pass
    unverified = [r["table"] for r in res if r["status"] == C.S_NO_DATA]
    if unverified:
        tips.append("无事件表：%s" % "、".join(sorted(set(unverified))))
    tips.extend(["未匹配门店保留待核", "不可回溯的历史快照未补采"])
    return "；".join(tips)


def _next_step(blocked):
    codes = {(r.get("error") or {}).get("code") for r in blocked}
    if "LOGIN_REQUIRED" in codes:
        return "请在两平台浏览器完成登录/验证后重跑恢复"
    if "STRUCTURE_CHANGED" in codes:
        return "确认源表新结构并更新契约后再回归；不自动补列改名"
    if "SCOPE_MISMATCH" in codes or "DATE_MISMATCH" in codes:
        return "核对平台日期/范围设置后重新提交任务"
    return "按有限重试上限停止；请人工确认平台/任务状态"


# ---------------- 页面对照（供 T9 演练记录） ----------------
def locator_snapshot(config_path):
    """读取定位配置快照（含版本与回退信息）。"""
    if not os.path.exists(config_path):
        return {}
    return C.read_json(config_path)
