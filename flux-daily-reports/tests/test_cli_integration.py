# -*- coding: utf-8 -*-
"""T10：主技能包 CLI 端到端集成（对应 spec S3-1 / S3-4 / S4-1）。

本文件把 CLI 当**真实使用者**来跑：在进程内调用 `flux.main(argv)`，
除「浏览器采集」被替换为合成替身外，其余环节（规划 / 验收 / 清洗 / 入库 /
自检 / 保留 / 汇报）全部走真实模块，不另起一套业务算法。

覆盖的关键不变量
1. `collect` 只采集，**库一行都不写**
2. `process` 只清洗入库，**一次浏览器都不碰**
3. 交棒前后是**同一个 batch_id**
4. **一个任务受阻 → 整批不得入库**
5. 自检与查询是**只读**的，不改库
6. 从**相对安装目录**也能跑通（路径按配置文件目录解析）
"""
import contextlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest

import tests
from tests import helpers as H
from common import contracts as C
import adapters
import db as DB
import flux
import run_daily

T = "2026-10-01"
DATA_ROOT = "_data"

# 历史基线（有序表头）——与合成注册表的 expected_cols 一致
HIST_RAW = [
    {"table": "门店经营指标", "header_row_count": 1,
     "samples": [{"header_rows": [["序号", "门店名称", "有效订单金额"]],
                  "width": 3, "sheets": ["sheet1"]}]},
    {"table": "评价数据", "header_row_count": 1,
     "samples": [{"header_rows": [["评价提交日期", "店铺ID", "订单商品"]],
                  "width": 3, "sheets": ["csv"]}]},
]

# 每张表的合成原件内容（行随任务日期变化，保证日期可信）
DATA = {
    "门店经营指标": {
        "header": ["序号", "门店名称", "有效订单金额"],
        "rows": lambda task: [["1", "示例门店A", "12.30"],
                              ["2", "示例门店B", "5.00"]],
    },
    "评价数据": {
        "header": ["评价提交日期", "店铺ID", "订单商品"],
        "rows": lambda task: [[task["business_date"], "1", "商品X"]],
    },
}

NODE = os.environ.get("FLUX_NODE") or shutil.which("node") or \
    r"C:\Program Files\nodejs\node.exe"
QUERY = os.path.join(os.path.dirname(os.path.abspath(tests.__file__)),
                     "..", "scripts", "query.mjs")


def node_available():
    if not (NODE and os.path.exists(NODE)):
        return False
    try:
        r = subprocess.run([NODE, "-e", "import('node:sqlite').then(()=>console.log('ok'))"],
                           capture_output=True, timeout=30)
        return b"ok" in r.stdout
    except Exception:                                  # noqa: BLE001
        return False


class FakeCollector:
    """采集替身：写合成原件、返回 TaskResult。**不做任何验收放宽**。"""

    def __init__(self, *, fail_tables=(), no_data_tables=()):
        self.calls = []
        self.fail_tables = set(fail_tables)
        self.no_data_tables = set(no_data_tables)

    def __call__(self, task, cfg):
        self.calls.append((task["platform"], task["table"], task["business_date"]))
        table = task["table"]
        if table in self.fail_tables:
            return {"task_key": task["task_key"], "status": C.S_BLOCKED, "files": [],
                    "evidence": {},
                    "error": {"code": "BROWSER_UNAVAILABLE", "detail": "替身：CDP 不可达"}}
        if table in self.no_data_tables:
            return {"task_key": task["task_key"], "status": C.S_NO_DATA, "files": [],
                    "evidence": {"platform_task_id": "P-1",
                                 "no_data_response": "该日期无数据"}, "error": None}
        spec = DATA[table]
        path = run_daily.raw_path(cfg, cfg["batch_id"], task, "csv")
        H.write_csv(path, spec["header"], spec["rows"](task))
        entry = {"path": path, "bytes": os.path.getsize(path),
                 "sha256": run_daily.sha256_of(path)}
        return {"task_key": task["task_key"], "status": C.S_DOWNLOADED,
                "files": [entry], "evidence": {}, "error": None}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="flux_cli_")
        self.install = os.path.join(self.tmp, "F.LUX技能包")   # 含空格/点的真实目录名
        self._write_json(os.path.join(self.install, "references", "table_registry.json"),
                         H.REGISTRY)
        self._write_json(os.path.join(self.install, "references", "cleaning_rules.json"),
                         H.RULES)
        self._write_json(os.path.join(self.install, "references", "historical_headers.json"),
                         HIST_RAW)
        shutil.copy2(os.path.join(tests.REFERENCES, "store_master.example.json"),
                     os.path.join(self.install, "references", "store_master.example.json"))
        self.cfg_path = self._write_json(os.path.join(self.install, "config.json"), {
            "data_root": "./_data",
            "ledger_path": "./_data/_ledger.json",
            "batch_dir": "./_data/_batches",
            "db_path": "./_data/flux.db",
            "backup_dir": "./_data/_backup",
            "rules_path": "./references/cleaning_rules.json",
            "master_path": "./references/store_master.example.json",
            "registry_path": "./references/table_registry.json",
            "hist_path": "./references/historical_headers.json",
            "scope_id": "all",
        })
        self.rules_path = os.path.join(self.install, "references", "cleaning_rules.json")
        self.master_path = os.path.join(self.install, "references",
                                        "store_master.example.json")
        self.db = os.path.join(self.install, DATA_ROOT, "flux.db")
        self._orig = adapters.collect_task

    def tearDown(self):
        adapters.collect_task = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------- 工具 ----------
    @staticmethod
    def _write_json(path, obj):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        return path

    def use(self, collector):
        adapters.collect_task = collector
        return collector

    def cli(self, *argv):
        """进程内跑 CLI，捕获 stdout/stderr；返回 (退出码, stdout, stderr)。"""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = flux.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def collect(self, collector=None):
        col = self.use(collector or FakeCollector())
        code, out, err = self.cli("collect", "--today", T, "--config", self.cfg_path)
        self.assertEqual(code, 0, "collect 失败：%s%s" % (out, err))
        return col, json.loads(out)

    def process(self, manifest_path, db=None):
        code, out, err = self.cli("process", "--manifest", manifest_path,
                                  "--rules", self.rules_path, "--master", self.master_path,
                                  "--db", db or self.db)
        return code, out, err

    def rows(self, table):
        con = sqlite3.connect(self.db)
        try:
            return con.execute('SELECT COUNT(*) FROM "%s"' % table).fetchone()[0]
        finally:
            con.close()


# ---------------- 环节隔离 ----------------
class TestStageIsolation(Base):
    def test_collect_writes_no_database_row(self):
        col, summary = self.collect()
        self.assertFalse(os.path.exists(self.db), "采集阶段不得创建数据库")
        self.assertEqual(summary["collection_status"], "complete")
        self.assertEqual(summary["complete"], summary["planned"])
        # 原件**逐批**落盘，且与计划任务数一一对应
        rawd = os.path.join(self.install, DATA_ROOT, "raw", summary["batch_id"])
        self.assertTrue(os.path.isdir(rawd))
        self.assertEqual(len(os.listdir(rawd)), summary["planned"])

    def test_process_makes_zero_browser_calls(self):
        col, summary = self.collect()
        seen = len(col.calls)
        self.assertGreater(seen, 0)
        code, out, err = self.process(summary["manifest"])
        self.assertEqual(code, 0, out + err)
        self.assertEqual(len(col.calls), seen, "入库阶段不得再触发采集")
        res = json.loads(out)
        self.assertGreater(res["inserted"], 0)
        self.assertEqual(self.rows("f_门店经营指标"), 2 * 7)   # 7 个日期 × 2 门店

    def test_check_gap_collects_nothing(self):
        col = self.use(FakeCollector())
        code, out, err = self.cli("check-gap", "--today", T, "--config", self.cfg_path)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(col.calls, [])
        self.assertFalse(os.path.exists(self.db))
        gap = json.loads(out)
        self.assertEqual(gap["gap_total"], gap["planned_tasks"])
        self.assertEqual(gap["gap_total"], 14)               # 2 表 × 7 日窗口


# ---------------- 交棒与闸门 ----------------
class TestHandoffAndGate(Base):
    def test_handoff_keeps_one_batch_id(self):
        _, summary = self.collect()
        batch_id = summary["batch_id"]
        code, out, err = self.process(summary["manifest"])
        self.assertEqual(code, 0, out + err)
        res = json.loads(out)
        self.assertEqual(res["batch_id"], batch_id)
        self.assertEqual(res["committed_version"], batch_id)
        con = sqlite3.connect(self.db)
        try:
            ids = [r[0] for r in con.execute("SELECT batch_id FROM _batch")]
        finally:
            con.close()
        self.assertEqual(ids, [batch_id])

    def test_one_blocked_task_blocks_the_whole_batch(self):
        col = self.use(FakeCollector(fail_tables={"评价数据"}))
        code, out, err = self.cli("collect", "--today", T, "--config", self.cfg_path)
        self.assertEqual(code, 1)                            # 采集即失败退出
        summary = json.loads(out)
        self.assertEqual(summary["collection_status"], "blocked")
        self.assertEqual(summary["blocked_or_failed"], 7)
        self.assertFalse(os.path.exists(self.db))
        # 全批次闸门：入库被**明确拒绝**，且不留下半成品
        code2, out2, err2 = self.process(summary["manifest"])
        self.assertEqual(code2, 2)
        self.assertIn("未全部合格", err2)
        self.assertFalse(os.path.exists(self.db))

    def test_no_data_task_needs_platform_evidence(self):
        """无事件不是「没采到」：有证据才算完成，无证据降级失败。"""
        col = self.use(FakeCollector(no_data_tables={"评价数据"}))
        code, out, err = self.cli("collect", "--today", T, "--config", self.cfg_path)
        self.assertEqual(code, 0, out + err)
        summary = json.loads(out)
        self.assertEqual(summary["no_data"], 7)
        self.assertEqual(summary["collection_status"], "complete")


# ---------------- 只读保证 ----------------
class TestReadOnly(Base):
    def _committed(self):
        _, summary = self.collect()
        code, out, err = self.process(summary["manifest"])
        self.assertEqual(code, 0, out + err)
        return summary

    def test_selfcheck_does_not_mutate(self):
        self._committed()
        before = DB.snapshot_of(self.db)
        code, out, err = self.cli("selfcheck", "--db", self.db, "--mode", "sample",
                                  "--sample-size", "5")
        self.assertEqual(code, 0, out + err)
        self.assertTrue(json.loads(out)["passed"])
        self.assertEqual(before, DB.snapshot_of(self.db))

    def test_selfcheck_detects_corruption_without_repairing(self):
        self._committed()
        con = sqlite3.connect(self.db)
        con.execute('UPDATE "f_门店经营指标" SET "有效订单金额"=? WHERE "门店标准ID"=?',
                    ("999.99", "S0001"))
        con.commit()
        con.close()
        code, out, err = self.cli("selfcheck", "--db", self.db)
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out)["passed"])
        # 只报告、不擅自回改
        con = sqlite3.connect(self.db)
        try:
            v = con.execute('SELECT "有效订单金额" FROM "f_门店经营指标" '
                            'WHERE "门店标准ID"="S0001"').fetchone()[0]
        finally:
            con.close()
        self.assertEqual(v, "999.99")

    @unittest.skipUnless(node_available(), "需要 Node 24+ 的 node:sqlite")
    def test_query_is_readonly(self):
        self._committed()
        before = DB.snapshot_of(self.db)
        r = subprocess.run([NODE, os.path.abspath(QUERY), "audit", "--db", self.db],
                           capture_output=True, text=True, timeout=120)
        self.assertTrue(r.stdout.strip(), r.stderr[:400])
        self.assertIn("integrity_check", json.loads(r.stdout))
        self.assertEqual(before, DB.snapshot_of(self.db))

    def test_missing_db_is_clear_error(self):
        code, out, err = self.cli("selfcheck", "--db", os.path.join(self.tmp, "nope.db"))
        self.assertEqual(code, 2)
        self.assertTrue(err.strip())


# ---------------- 保留期 ----------------
class TestRetention(Base):
    def test_retain_dry_run_previews_then_backs_up(self):
        _, summary = self.collect()
        code, out, err = self.process(summary["manifest"])
        self.assertEqual(code, 0, out + err)
        backup_dir = os.path.join(self.install, DATA_ROOT, "_backup")
        code, out, err = self.cli("retain", "--db", self.db, "--today", T,
                                  "--backup-dir", backup_dir, "--dry-run")
        self.assertEqual(code, 0, out + err)
        preview = json.loads(out)
        self.assertIsNone(preview["backup"])
        self.assertEqual(preview["deleted"], 0)
        self.assertEqual(preview["preview"]["retention_start"], "2025-10-01")
        self.assertEqual(preview["preview"]["retention_end"], "2026-09-30")
        self.assertEqual(os.listdir(backup_dir) if os.path.isdir(backup_dir) else [], [])

        code, out, err = self.cli("retain", "--db", self.db, "--today", T,
                                  "--backup-dir", backup_dir)
        self.assertEqual(code, 0, out + err)
        done = json.loads(out)
        self.assertTrue(done["backup"]["sha256"])
        backups = os.listdir(backup_dir)
        self.assertEqual(backups, ["flux_20261001.db"])
        # 备份是一致性副本，可直接打开
        con = sqlite3.connect(os.path.join(backup_dir, backups[0]))
        try:
            n = con.execute('SELECT COUNT(*) FROM "f_门店经营指标"').fetchone()[0]
        finally:
            con.close()
        self.assertEqual(n, 2 * 7)


# ---------------- 恢复与汇报 ----------------
class TestResumeAndReport(Base):
    def test_resume_retries_only_blocked_and_reuses_batch(self):
        blocked = FakeCollector(fail_tables={"评价数据"})
        self.use(blocked)
        code, out, err = self.cli("collect", "--today", T, "--config", self.cfg_path)
        self.assertEqual(code, 1)
        summary = json.loads(out)
        batch_id = summary["batch_id"]
        retried_before = len(blocked.calls)

        # 第二次恢复：评价数据已恢复正常
        healthy = self.use(FakeCollector())
        code, out, err = self.cli("resume", "--manifest", summary["manifest"],
                                  "--config", self.cfg_path, "--rules", self.rules_path,
                                  "--master", self.master_path, "--db", self.db)
        self.assertEqual(code, 0, out + err)
        # 只重跑受阻的那 7 个任务，不重采已合格的门店经营指标
        self.assertEqual(len(healthy.calls), 7)
        self.assertTrue(all(c[1] == "评价数据" for c in healthy.calls))
        self.assertGreater(len(healthy.calls), 0)
        self.assertLess(retried_before, retried_before + len(healthy.calls))
        # 沿用同一批次身份
        con = sqlite3.connect(self.db)
        try:
            ids = [r[0] for r in con.execute("SELECT batch_id FROM _batch")]
        finally:
            con.close()
        self.assertEqual(ids, [batch_id])

    def test_report_on_blocked_batch_says_unfinished(self):
        self.use(FakeCollector(fail_tables={"门店经营指标"}))
        code, out, err = self.cli("collect", "--today", T, "--config", self.cfg_path)
        self.assertEqual(code, 1)
        manifest_path = json.loads(out)["manifest"]
        code, out, err = self.cli("report", "--manifest", manifest_path)
        self.assertEqual(code, 0, out + err)
        self.assertIn("【未完成】", out)
        self.assertIn("本次库是否提交：否", out)
        self.assertIn("沿用上一成功版本", out)
        self.assertNotIn("【成功】", out)


# ---------------- 整体链路 ----------------
class TestFullRun(Base):
    def test_run_end_to_end_offline(self):
        col = self.use(FakeCollector())
        code, out, err = self.cli("run", "--today", T, "--config", self.cfg_path,
                                  "--rules", self.rules_path,
                                  "--master", self.master_path, "--db", self.db)
        self.assertEqual(code, 0, out + err)
        self.assertIn("【成功】", out)
        self.assertIn("入库：新增", out)
        self.assertIn("自检：", out)
        con = sqlite3.connect(self.db)
        try:
            batches = con.execute("SELECT batch_id, status FROM _batch").fetchall()
        finally:
            con.close()
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0][1], "committed")
        self.assertEqual(self.rows("f_评价数据"), 7)

    def test_run_stops_at_first_failed_stage(self):
        self.use(FakeCollector(fail_tables={"评价数据"}))
        code, out, err = self.cli("run", "--today", T, "--config", self.cfg_path,
                                  "--rules", self.rules_path,
                                  "--master", self.master_path, "--db", self.db)
        self.assertEqual(code, 1)
        self.assertIn("【未完成】", out)
        self.assertFalse(os.path.exists(self.db))            # 未入库


# ---------------- 安装目录相对路径 ----------------
class TestRelativeInstall(Base):
    def test_runs_from_relative_install_dir(self):
        self.use(FakeCollector())
        cwd = os.getcwd()
        os.chdir(self.install)
        try:
            code, out, err = self.cli("collect", "--today", T, "--config", "config.json")
        finally:
            os.chdir(cwd)
        self.assertEqual(code, 0, out + err)
        # 相对 data_root 落在**安装目录**下，而不是当前工作目录
        self.assertTrue(os.path.isdir(os.path.join(self.install, DATA_ROOT, "raw")))

    def test_bad_config_date_is_reported_not_crashed(self):
        self.use(FakeCollector())
        code, out, err = self.cli("collect", "--today", "2026-02-30",
                                  "--config", self.cfg_path)
        self.assertEqual(code, 2)
        self.assertIn("2026-02-30", err)

    def test_missing_registry_is_reported(self):
        bad = self._write_json(os.path.join(self.tmp, "bad.json"),
                               {"registry_path": "./nope.json"})
        code, out, err = self.cli("check-gap", "--today", T, "--config", bad)
        self.assertEqual(code, 2)
        self.assertTrue(err.strip())


if __name__ == "__main__":
    unittest.main()
