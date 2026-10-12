# -*- coding: utf-8 -*-
"""T8/T9：恢复、有限重试、页面/列变化与汇报（对应 spec S3-1~S3-3）。"""
import hashlib
import os
import shutil
import tempfile
import unittest

import tests
from tests import helpers as H
from clean import load_master, prepare_batch
from common import contracts as C
from recover_report import (classify_change, decide_recovery, render_report,
                            resume_batch, retry_plan, sync_processing_status,
                            validate_locator_change)
import db as DB

T = "2026-10-01"


def _sha_of(path):
    """算原件 SHA-256（句柄随 with 关闭，避免测试留下未关闭文件）。"""
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="flux_rec_")
        self.cfg = H.config(self.tmp)
        self.db = os.path.join(self.tmp, "flux.db")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def manifest(self, statuses=None, evidence=None):
        t1 = H.make_task("门店经营指标", "2026-09-30")
        t2 = H.make_task("评价数据", "2026-09-29")
        statuses = statuses or {}
        evidence = evidence or {}
        files = {}
        if statuses.get("门店经营指标") == C.S_DOWNLOADED:
            files["门店经营指标"] = [H.qianniu_file(
                self.tmp, [["1", "示例门店A", "12.30"]])]
        return H.collect([t1, t2], self.cfg, files,
                         statuses=statuses, evidence=evidence)


class TestRetryPolicy(Base):
    def test_default_cap_is_three_attempts_then_stop(self):
        mf = self.manifest(statuses={"评价数据": C.S_BLOCKED})
        seq = retry_plan(mf, {"code": "TIMEOUT"})
        total_attempts = len(seq)
        self.assertEqual(total_attempts, 3)
        self.assertEqual(seq[-1]["decision"], "stop")
        self.assertEqual([s["decision"] for s in seq], ["retry", "retry", "stop"])

    def test_no_infinite_retry(self):
        mf = self.manifest()
        d = decide_recovery(mf, {"code": "PLATFORM_ERROR", "attempts": 5})
        self.assertEqual(d["action"], "stop")
        self.assertEqual(d["attempts_left"], 0)

    def test_login_expired_waits_for_user(self):
        mf = self.manifest(statuses={"评价数据": C.S_BLOCKED})
        d = decide_recovery(mf, {"code": "LOGIN_REQUIRED", "task_key": "k"})
        self.assertEqual(d["action"], "wait_user")
        self.assertEqual(d["task_key"], "k")

    def test_structure_change_stops_and_needs_human(self):
        mf = self.manifest(statuses={"门店经营指标": C.S_BLOCKED})
        d = decide_recovery(mf, {"code": "STRUCTURE_CHANGED"})
        self.assertEqual(d["action"], "stop")
        self.assertIn("人工", d["reason"])

    def test_committed_batch_is_skipped(self):
        mf = self.manifest()
        mf["committed_version"] = "B1"
        d = decide_recovery(mf, {"code": "TIMEOUT"})
        self.assertEqual(d["action"], "skip_committed")


class TestResume(Base):
    def test_resume_only_reruns_blocked_and_keeps_business_today(self):
        t1 = H.make_task("门店经营指标", "2026-09-30")
        t2 = H.make_task("评价数据", "2026-09-29")
        p = H.qianniu_file(self.tmp, [["1", "示例门店A", "12.30"]])
        mf = H.collect([t1, t2], self.cfg, {"门店经营指标": [p]},
                       statuses={"评价数据": C.S_BLOCKED})
        original_t = mf["business_today"]
        self.assertEqual(mf["collection_status"], "blocked")

        calls = []

        def collector(task, cfg):
            calls.append(task["table"])
            if task["table"] == "评价数据":
                return {"task_key": task["task_key"], "status": C.S_NO_DATA,
                        "files": [], "evidence": {"platform_task_id": "P-2",
                                                  "no_data_response": "该日无评价"},
                        "error": None}
            raise AssertionError("已验收的任务不应重采：%s" % task["table"])

        resumed = resume_batch(mf, self.cfg, collector=collector, hist=H.HIST)
        self.assertEqual(resumed["business_today"], original_t)
        self.assertEqual(resumed["batch_id"], mf["batch_id"])   # 沿用同一批次身份
        self.assertEqual(calls, ["评价数据"])                     # 只跑受阻环节
        self.assertTrue(C.is_collection_complete(resumed))
        self.assertEqual(resumed["resumed"]["still_incomplete"], 0)

    def test_resume_does_not_touch_committed_db(self):
        mf = self.manifest(statuses={"评价数据": C.S_NO_DATA},
                           evidence={"评价数据": {"platform_task_id": "P-1",
                                                 "no_data_response": "无"}})
        mf = H.collect([H.make_task("门店经营指标", "2026-09-30")], self.cfg,
                       {"门店经营指标": [H.qianniu_file(
                           self.tmp, [["1", "示例门店A", "12.30"]])]})
        DB.apply_batch(self.db, prepare_batch(mf, H.rules(), load_master()), T)
        mf["committed_version"] = mf["batch_id"]
        before = DB.snapshot_of(self.db)
        out = resume_batch(mf, self.cfg, collector=lambda t, c: None, hist=H.HIST)
        self.assertEqual(out["resumed"], "skipped_already_committed")
        self.assertEqual(DB.snapshot_of(self.db), before)

    def test_resume_recovers_after_login(self):
        t1 = H.make_task("门店经营指标", "2026-09-30")
        mf = H.collect([t1], self.cfg, {}, statuses={"门店经营指标": C.S_BLOCKED})
        self.assertEqual(mf["collection_status"], "blocked")
        p = H.qianniu_file(self.tmp, [["1", "示例门店A", "12.30"]])

        def collector(task, cfg):
            return {"task_key": task["task_key"], "status": C.S_DOWNLOADED,
                    "files": [{"path": p, "bytes": os.path.getsize(p),
                               "sha256": _sha_of(p)}],
                    "evidence": {"platform_task_id": "90001"}, "error": None}

        resumed = resume_batch(mf, self.cfg, collector=collector, hist=H.HIST)
        self.assertTrue(C.is_collection_complete(resumed))
        self.assertEqual(resumed["resumed"]["retried_tasks"], 1)


class TestPageAndColumnChange(Base):
    def test_layout_change_accepted_when_unique_and_structure_same(self):
        old = {"selector": "#old-btn", "version": "locator-1"}
        cand = {"selector": "#new-btn"}
        res = validate_locator_change(old, cand, {"matches": 1, "structure_same": True})
        self.assertTrue(res["accepted"])
        self.assertEqual(res["rollback"], old)              # 旧配置可回退
        self.assertTrue(res["version"])

    def test_ambiguous_locator_rejected(self):
        ambiguous_locator = validate_locator_change(
            {"selector": "#a"}, {"selector": ".btn"},
            {"matches": 3, "structure_same": True})
        self.assertFalse(ambiguous_locator["accepted"])

    def test_structural_mismatch_rejected(self):
        res = validate_locator_change({"selector": "#a"}, {"selector": ".btn"},
                                      {"matches": 1, "structure_same": False})
        self.assertFalse(res["accepted"])

    def test_column_change_blocks_processing(self):
        t1 = H.make_task("门店经营指标", "2026-09-30")
        mf = H.collect([t1], self.cfg, {}, statuses={"门店经营指标": C.S_BLOCKED})
        mf["results"][0]["error"] = {"code": "STRUCTURE_CHANGED",
                                     "detail": "第 5 列 有效订单金额 -> 订单金额"}
        column_changed = sync_processing_status(mf)
        self.assertEqual(column_changed["processing_status"], "blocked")
        with self.assertRaises(ValueError):
            prepare_batch(column_changed, H.rules(), load_master())

    def test_change_classification(self):
        self.assertTrue(classify_change("layout")["auto"])
        self.assertFalse(classify_change("columns")["auto"])
        self.assertFalse(classify_change("semantics")["auto"])
        with self.assertRaises(ValueError):
            classify_change("unknown")


class TestReport(Base):
    def test_collect_only_report_never_claims_loaded(self):
        mf = H.collect([H.make_task("门店经营指标", "2026-09-30")], self.cfg,
                       {"门店经营指标": [H.qianniu_file(
                           self.tmp, [["1", "示例门店A", "12.30"]])]})
        collect_only_report = render_report(mf)
        self.assertIn("【成功】", collect_only_report)
        self.assertNotIn("已入库", collect_only_report)
        self.assertIn("入库：本次未执行（仅采集环节）", collect_only_report)

    def test_full_report_includes_write_and_check(self):
        mf = H.collect([H.make_task("门店经营指标", "2026-09-30")], self.cfg,
                       {"门店经营指标": [H.qianniu_file(
                           self.tmp, [["1", "示例门店A", "12.30"]])]})
        wr = DB.apply_batch(self.db, prepare_batch(mf, H.rules(), load_master()), T)
        from selfcheck import check_database
        ck = check_database(self.db, {"tables": ["门店经营指标"]})
        rep = render_report(mf, write_result=wr, check_result=ck)
        self.assertIn("入库：新增 1", rep)
        self.assertIn("自检", rep)

    def test_post_commit_failure_report_says_committed(self):
        mf = H.collect([H.make_task("门店经营指标", "2026-09-30")], self.cfg,
                       {"门店经营指标": [H.qianniu_file(
                           self.tmp, [["1", "示例门店A", "12.30"]])]})
        wr = DB.apply_batch(self.db, prepare_batch(mf, H.rules(), load_master()), T)
        mf["committed_version"] = wr["committed_version"]
        mf["results"][0]["status"] = C.S_BLOCKED
        mf["results"][0]["error"] = {"code": "PARSE_FAILED", "detail": "提交后自检发现差异"}
        post_commit_failure_report = render_report(mf, write_result=wr)
        self.assertIn("本次库是否提交：是", post_commit_failure_report)
        self.assertIn("【未完成】", post_commit_failure_report)

    def test_failed_report_includes_next_step_and_no_credentials(self):
        mf = self.manifest(statuses={"评价数据": C.S_BLOCKED})
        mf["results"][1]["error"] = {"code": "LOGIN_REQUIRED", "detail": "登录页"}
        rep = render_report(mf)
        self.assertIn("【未完成】", rep)
        self.assertIn("登录", rep)
        self.assertNotIn("cookie", rep.lower())
        self.assertNotIn("token", rep.lower())

    def test_no_data_reported_as_such(self):
        mf = H.collect([H.make_task("评价数据", "2026-09-29")], self.cfg, {},
                       statuses={"评价数据": C.S_NO_DATA},
                       evidence={"评价数据": {"platform_task_id": "P-1",
                                             "no_data_response": "该日无评价数据"}})
        rep = render_report(mf)
        self.assertIn("确认无事件 1", rep)


if __name__ == "__main__":
    unittest.main()
