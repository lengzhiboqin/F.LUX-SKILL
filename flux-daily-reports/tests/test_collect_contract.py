# -*- coding: utf-8 -*-
"""T2/T3：采集契约与原件验收（对应 spec S1-2 ~ S1-4）。

全部用**合成原件**驱动，不连平台；平台真实三轮由宿主实测（见验收索引）。
"""
import csv
import json
import os
import shutil
import tempfile
import unittest

import tests  # noqa: F401
from adapters import map_qianniu_rec, map_shangou_result
from common import contracts as C
from run_daily import run_collection, validate_raw, plan_daily
from common import registry as REG

# 合成注册表：两张表足够覆盖「普通日表 + 事件表」两条路径
FAKE_REG = {
    "contract_version": "v1",
    "snapshot_window": {"days": 30, "offset": 1},
    "tables": [
        {"table": "门店经营指标", "platform": "牵牛花", "type": "daily",
         "default_offset_days": 1, "expected_cols": 3, "header_rows": 1},
        {"table": "评价数据", "platform": "闪购", "type": "event",
         "default_offset_days": 2, "expected_cols": 2, "header_rows": 1},
    ],
}
FAKE_HIST = {
    "门店经营指标": {"header_rows": [["序号", "门店名称", "有效订单金额"]],
                 "width": 3, "sheets": ["sheet1"]},
    "评价数据": {"header_rows": [["下单时间", "评价内容"]], "width": 2, "sheets": ["csv"]},
}
T = "2026-10-01"


def write_csv(path, header, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="flux_collect_")
        self.reg = dict(FAKE_REG)
        self.reg["_index"] = {t["table"]: t for t in self.reg["tables"]}
        self.cfg = {"data_root": os.path.join(self.tmp, "data"),
                    "batch_id": "B20261001-daily-test",
                    "_registry": self.reg}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def task(self, table, date, scope_id="all"):
        e = self.reg["_index"][table]
        return C.make_task(e["platform"], table, date, scope={"scope_id": scope_id})

    def raw(self, task, status, files=(), evidence=None, error=None):
        r = {"task_key": task["task_key"], "status": status, "files": list(files),
             "evidence": evidence or {}, "error": error}
        return r

    def run_one(self, task, collector):
        plan = C.make_plan(T, "targeted", [task], [])
        mf = run_collection(plan, self.cfg, collector=collector, hist=FAKE_HIST)
        return mf["results"][0]


class TestQianniuMapping(Base):
    def test_ok_status_maps_to_downloaded_with_submitted_task_id(self):
        task = self.task("门店经营指标", "2026-09-30")
        p = write_csv(os.path.join(self.tmp, "门店经营指标_20260930.csv"),
                      ["序号", "门店名称", "有效订单金额"], [["1", "A店", "12.30"]])
        rec = {"status": "ok", "taskId": "90001", "taskName": "导出门店经营指标20260930_20260930",
               "file": p, "cols": 3, "md5": "abc"}
        res = map_qianniu_rec(task, rec, lambda *a: p)
        self.assertEqual(res["status"], C.S_DOWNLOADED)
        self.assertEqual(res["evidence"]["platform_task_id"], "90001")
        self.assertEqual(res["files"][0]["bytes"], os.path.getsize(p))

    def test_column_count_anomaly_is_blocked_not_ok(self):
        task = self.task("门店经营指标", "2026-09-30")
        rec = {"status": "列数异常", "detail": "期望 19 列，实际 16 列"}
        res = map_qianniu_rec(task, rec, lambda *a: None)
        self.assertEqual(res["status"], C.S_BLOCKED)
        self.assertEqual(res["error"]["code"], "STRUCTURE_CHANGED")

    def test_platform_task_id_must_be_the_new_one_not_stale(self):
        from qianniu_collect import pick_latest_new_task
        tasks = [
            {"id": "100", "n": "导出门店经营指标20260930_20260930", "st": "已完成"},
            {"id": "101", "n": "导出门店经营指标20260930_20260930", "st": "已完成"},
        ]
        baseline = {"100"}                       # 提交前的旧任务
        got = pick_latest_new_task(tasks, "导出门店经营指标", "20260930", baseline=baseline)
        self.assertEqual(str(got["id"]), "101")   # 只认提交之后新建的那条


class TestShangouMapping(Base):
    def test_no_data_keeps_platform_evidence(self):
        task = self.task("评价数据", "2026-09-29")
        res = map_shangou_result(task, {"file": None, "no_data": True,
                                        "note": "导出任务创建失败无有效数据", "date": "2026-09-29"})
        self.assertEqual(res["status"], C.S_NO_DATA)
        self.assertIn("无有效数据", res["evidence"]["no_data_response"])

    def test_zero_byte_is_not_no_data(self):
        task = self.task("评价数据", "2026-09-29")
        p = os.path.join(self.tmp, "评价数据_20260929.csv")
        open(p, "w").close()
        res = map_shangou_result(task, {"file": p, "rows": 0, "cols": 0, "date": "2026-09-29"})
        self.assertNotEqual(res["status"], C.S_NO_DATA)
        self.assertEqual(res["error"]["code"], "ZERO_BYTES")


class TestValidateRaw(Base):
    def test_accepts_matching_structure(self):
        task = self.task("门店经营指标", "2026-09-30")
        p = write_csv(os.path.join(self.tmp, "门店经营指标_20260930.csv"),
                      ["序号", "门店名称", "有效订单金额"], [["1", "A店", "12.30"]])
        res = self.run_one(task, lambda t, c: self.raw(
            t, C.S_DOWNLOADED, files=[{"path": p, "bytes": os.path.getsize(p), "sha256": ""}]))
        self.assertEqual(res["status"], C.S_VALIDATED)
        self.assertEqual(res["evidence"]["structure"][0]["rows"], 1)

    def test_reordered_header_is_blocked(self):
        task = self.task("门店经营指标", "2026-09-30")
        p = write_csv(os.path.join(self.tmp, "门店经营指标_20260930.csv"),
                      ["门店名称", "序号", "有效订单金额"], [["A店", "1", "12.30"]])
        res = self.run_one(task, lambda t, c: self.raw(
            t, C.S_DOWNLOADED, files=[{"path": p, "bytes": os.path.getsize(p), "sha256": ""}]))
        self.assertEqual(res["status"], C.S_BLOCKED)
        self.assertEqual(res["error"]["code"], "STRUCTURE_CHANGED")

    def test_zero_byte_file_fails(self):
        task = self.task("门店经营指标", "2026-09-30")
        p = os.path.join(self.tmp, "门店经营指标_20260930.csv")
        open(p, "w").close()
        res = self.run_one(task, lambda t, c: self.raw(
            t, C.S_DOWNLOADED, files=[{"path": p, "bytes": 0, "sha256": ""}]))
        self.assertNotEqual(res["status"], C.S_NO_DATA)
        self.assertEqual(res["error"]["code"], "ZERO_BYTES")

    def test_header_only_is_blocked_not_no_data(self):
        task = self.task("门店经营指标", "2026-09-30")
        p = write_csv(os.path.join(self.tmp, "门店经营指标_20260930.csv"),
                      ["序号", "门店名称", "有效订单金额"], [])
        res = self.run_one(task, lambda t, c: self.raw(
            t, C.S_DOWNLOADED, files=[{"path": p, "bytes": os.path.getsize(p), "sha256": ""}]))
        self.assertEqual(res["status"], C.S_BLOCKED)
        self.assertEqual(res["error"]["code"], "EMPTY_DOWNLOAD")

    def test_wrong_date_in_filename_is_blocked(self):
        task = self.task("门店经营指标", "2026-09-30")
        p = write_csv(os.path.join(self.tmp, "门店经营指标_20260929.csv"),
                      ["序号", "门店名称", "有效订单金额"], [["1", "A店", "1"]])
        res = self.run_one(task, lambda t, c: self.raw(
            t, C.S_DOWNLOADED, files=[{"path": p, "bytes": os.path.getsize(p), "sha256": ""}]))
        self.assertEqual(res["status"], C.S_BLOCKED)
        self.assertEqual(res["error"]["code"], "DATE_MISMATCH")

    def test_wrong_channel_scope_is_blocked(self):
        task = self.task("门店经营指标", "2026-09-30", scope_id="channel:100")
        p = write_csv(os.path.join(self.tmp, "门店经营指标_20260930.csv"),
                      ["序号", "门店名称", "有效订单金额"], [["1", "A店", "1"]])
        res = self.run_one(task, lambda t, c: self.raw(
            t, C.S_DOWNLOADED, files=[{"path": p, "bytes": os.path.getsize(p), "sha256": ""}]))
        self.assertEqual(res["status"], C.S_BLOCKED)
        self.assertEqual(res["error"]["code"], "SCOPE_MISMATCH")

    def test_scoped_no_data_with_evidence_is_complete(self):
        task = self.task("评价数据", "2026-09-29")
        res = self.run_one(task, lambda t, c: self.raw(
            t, C.S_NO_DATA, evidence={"platform_task_id": "P-1",
                                      "no_data_response": "该日期无评价数据"}))
        self.assertEqual(res["status"], C.S_NO_DATA)

    def test_unrelated_no_data_without_evidence_fails(self):
        task = self.task("评价数据", "2026-09-29")
        res = self.run_one(task, lambda t, c: self.raw(t, C.S_NO_DATA, evidence={}))
        self.assertNotEqual(res["status"], C.S_NO_DATA)
        self.assertEqual(res["error"]["code"], "NO_DATA_UNVERIFIED")

    def test_login_expired_is_blocked_with_login_required(self):
        task = self.task("评价数据", "2026-09-29")
        res = self.run_one(task, lambda t, c: self.raw(
            t, C.S_BLOCKED, error={"code": "LOGIN_REQUIRED", "detail": "落在登录页"}))
        self.assertEqual(res["error"]["code"], "LOGIN_REQUIRED")
        self.assertEqual(res["status"], C.S_BLOCKED)

    def test_sha256_mismatch_fails(self):
        task = self.task("门店经营指标", "2026-09-30")
        p = write_csv(os.path.join(self.tmp, "门店经营指标_20260930.csv"),
                      ["序号", "门店名称", "有效订单金额"], [["1", "A店", "1"]])
        res = self.run_one(task, lambda t, c: self.raw(
            t, C.S_DOWNLOADED,
            files=[{"path": p, "bytes": os.path.getsize(p), "sha256": "deadbeef"}]))
        self.assertEqual(res["status"], C.S_FAILED)
        self.assertEqual(res["error"]["code"], "PARSE_FAILED")


class TestBatchGate(Base):
    def test_one_failure_blocks_whole_batch(self):
        t1 = self.task("门店经营指标", "2026-09-30")
        t2 = self.task("评价数据", "2026-09-29")
        good = write_csv(os.path.join(self.tmp, "门店经营指标_20260930.csv"),
                         ["序号", "门店名称", "有效订单金额"], [["1", "A店", "1"]])

        def collector(task, cfg):
            if task["table"] == "门店经营指标":
                return self.raw(task, C.S_DOWNLOADED,
                                files=[{"path": good, "bytes": os.path.getsize(good),
                                        "sha256": ""}])
            return self.raw(task, C.S_BLOCKED, error={"code": "LOGIN_REQUIRED"})

        plan = C.make_plan(T, "targeted", [t1, t2], [])
        mf = run_collection(plan, self.cfg, collector=collector, hist=FAKE_HIST)
        self.assertEqual(mf["collection_status"], "blocked")
        self.assertFalse(C.is_collection_complete(mf))

    def test_manifest_validates_result_to_task_identity(self):
        task = self.task("门店经营指标", "2026-09-30")
        p = write_csv(os.path.join(self.tmp, "门店经营指标_20260930.csv"),
                      ["序号", "门店名称", "有效订单金额"], [["1", "A店", "1"]])
        plan = C.make_plan(T, "targeted", [task], [])
        mf = run_collection(plan, self.cfg, hist=FAKE_HIST,
                            collector=lambda t, c: self.raw(
                                t, C.S_DOWNLOADED,
                                files=[{"path": p, "bytes": os.path.getsize(p),
                                        "sha256": ""}]))
        self.assertTrue(C.validate_manifest(mf))
        bad = dict(mf)
        bad["results"] = [dict(r) for r in mf["results"]]
        bad["results"][0]["task_key"] = "not-in-plan"
        with self.assertRaises(ValueError):
            C.validate_manifest(bad)

    def test_ledger_updated_only_for_complete_tasks(self):
        task = self.task("评价数据", "2026-09-29")
        plan = C.make_plan(T, "targeted", [task], [])
        led = {}
        run_collection(plan, self.cfg, hist=FAKE_HIST, ledger=led,
                       collector=lambda t, c: self.raw(
                           t, C.S_NO_DATA,
                           evidence={"platform_task_id": "P-9", "no_data_response": "无"}))
        self.assertTrue(C.ledger_is_complete(led, task["task_key"]))


class TestLoginAndAntiBotMapping(Base):
    """S1-2：登录/反爬必须**停下请用户接管**，且**不自动清 Cookie**。"""

    def task(self, table="评价数据", date="2026-09-29"):
        e = self.reg["_index"][table]
        return C.make_task(e["platform"], table, date, scope={"scope_id": "all"})

    def _map(self, exc, table="评价数据"):
        import adapters
        orig = adapters._collect_shangou
        adapters._collect_shangou = lambda t, c: (_ for _ in ()).throw(exc)
        try:
            return adapters.collect_task(self.task(table), self.cfg)
        finally:
            adapters._collect_shangou = orig

    def test_browser_unreachable_is_blocked(self):
        import adapters
        res = self._map(adapters.BrowserUnavailable("CDP 无响应"))
        self.assertEqual(res["status"], C.S_BLOCKED)
        self.assertEqual(res["error"]["code"], "BROWSER_UNAVAILABLE")
        self.assertTrue(res["error"]["action"])          # 给出用户可执行的下一步

    def test_anti_bot_runtime_error_becomes_login_required(self):
        res = self._map(RuntimeError("[ANTI_BOT] 美团反爬拦截，请重新登录后重试"))
        self.assertEqual(res["status"], C.S_BLOCKED)
        self.assertEqual(res["error"]["code"], "LOGIN_REQUIRED")
        self.assertIn("请", res["error"]["action"])

    def test_cookie_clear_blocked_message_maps_to_login_required(self):
        res = self._map(RuntimeError("[COOKIE_CLEAR_BLOCKED] 默认不自动清 Cookie"))
        self.assertEqual(res["error"]["code"], "LOGIN_REQUIRED")

    def test_plain_runtime_error_is_failed_not_blocked(self):
        res = self._map(RuntimeError("页面元素找不到"))
        self.assertEqual(res["status"], C.S_FAILED)
        self.assertEqual(res["error"]["code"], "PLATFORM_ERROR")

    def test_clear_cookies_refuses_by_default(self):
        try:
            import shangou_report_download as sg
        except ImportError as e:                          # 无 playwright 环境跳过，不算失败
            self.skipTest("未装 playwright：%s" % e)
        self.assertFalse(sg.ALLOW_AUTO_CLEAR_COOKIES)
        with self.assertRaises(RuntimeError) as cm:
            sg.clear_cookies_and_relogin(None, None)     # 守卫在触碰 page 之前就拦下
        self.assertIn("COOKIE_CLEAR_BLOCKED", str(cm.exception))

    def test_targeted_shangou_gen1_date_is_honored_not_faked(self):
        """v0.2：闪购 gen1 表定向回采**按指定日期精确命中**，不冒用平台「昨日」。"""
        import adapters
        cfg = dict(self.cfg)
        cfg["explicit_date"] = True
        res = adapters.collect_task(self.task(), cfg)
        if res["status"] == C.S_BLOCKED and res["error"]["code"] in (
                "BROWSER_UNAVAILABLE", "LOGIN_REQUIRED"):
            self.skipTest("无可用已登录浏览器：%s" % res["error"]["code"])
        # 定向日回采不再被拒绝（v0.1 会 blocked），且必须落在**请求日**而非平台「昨日」
        self.assertIn(res["status"], (C.S_DOWNLOADED, C.S_VALIDATED))
        self.assertTrue(res["files"])
        self.assertIn("2026-09-29", res["files"][0]["path"])

    def test_targeted_shangou_gen2_date_is_refused_not_faked(self):
        """gen2 流量表无法按指定日期采集 → 明确 UNSUPPORTED_DOWNLOAD，不冒用昨日数据。"""
        import adapters
        cfg = dict(self.cfg)
        cfg["explicit_date"] = True
        # gen2 表不在测试注册表里，直接按平台+表名构造任务
        res = adapters.collect_task(
            C.make_task("闪购", "流量明细(新)", "2026-09-29", scope={"scope_id": "all"}),
            cfg)
        self.assertEqual(res["status"], C.S_BLOCKED)
        self.assertEqual(res["error"]["code"], "UNSUPPORTED_DOWNLOAD")
        self.assertEqual(res["files"], [])
        self.assertIn("2026-09-29", res["error"]["action"])


if __name__ == "__main__":
    unittest.main()
