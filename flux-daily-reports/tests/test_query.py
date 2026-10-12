# -*- coding: utf-8 -*-
"""T6：只读查询（对应 spec S2-1「只读查询能按 AI 调用需要定位数据」）。"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

import tests
from tests import helpers as H
from clean import load_master, prepare_batch
import db as DB

NODE = os.environ.get("FLUX_NODE") or shutil.which("node") or \
    r"C:\Program Files\nodejs\node.exe"
QUERY = os.path.join(os.path.dirname(os.path.abspath(tests.__file__)),
                     "..", "scripts", "query.mjs")
T = "2026-10-01"


def node_available():
    if not (NODE and os.path.exists(NODE)):
        return False
    try:
        r = subprocess.run([NODE, "-e", "import('node:sqlite').then(()=>console.log('ok'))"],
                           capture_output=True, timeout=30)
        return b"ok" in r.stdout
    except Exception:                                  # noqa: BLE001
        return False


def run_query(*argv):
    r = subprocess.run([NODE, os.path.abspath(QUERY)] + list(argv),
                       capture_output=True, text=True, timeout=120)
    if not r.stdout.strip():
        raise AssertionError("查询无输出：%s" % r.stderr[:400])
    return json.loads(r.stdout)


@unittest.skipUnless(node_available(), "需要 Node 24+ 的 node:sqlite")
class TestQuery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="flux_q_")
        self.db = os.path.join(self.tmp, "flux.db")
        self.cfg = H.config(self.tmp)
        self.src_sha = None

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self, scope_id="channel:100"):
        name = "门店经营指标_%s.csv" if scope_id == "all" else "门店经营指标_%s_ch100.csv"
        p = H.qianniu_file(self.tmp, [["1", "示例门店A", "12.30"],
                                      ["2", "示例门店B", "5.00"]],
                           date="20260930", name=name)
        task = H.make_task("门店经营指标", "2026-09-30", scope_id=scope_id)
        mf = H.collect([task], self.cfg, {"门店经营指标": [p]})
        self.src_sha = mf["results"][0]["files"][0]["sha256"]
        DB.apply_batch(self.db, prepare_batch(mf, H.rules(), load_master()), T)
        return self.src_sha

    def test_help_needs_no_db(self):
        res = run_query("help")
        self.assertIn("commands", res)
        self.assertIn("get", res["commands"])

    def test_get_returns_scope_and_source_sha(self):
        sha = self.build("channel:100")
        res = run_query("get", "--db", self.db, "--table", "f_门店经营指标",
                        "--metric", "有效订单金额", "--scope", "channel:100")
        self.assertEqual(res["count"], 2)
        self.assertEqual(res["items"][0]["scope_id"], "channel:100")
        self.assertEqual(res["items"][0]["source_sha256"], sha)
        self.assertEqual(res["items"][0]["status"], "formal")
        self.assertEqual(res["items"][0]["unit"], "元")

    def test_stores_and_catalog_and_status(self):
        self.build()
        cat = run_query("catalog", "--db", self.db, "--table", "f_门店经营指标")
        names = {i["column_name"] for i in cat["items"]}
        self.assertIn("有效订单金额", names)
        st = run_query("status", "--db", self.db)
        self.assertTrue(any(t["table"] == "f_门店经营指标" for t in st["tables"]))
        batch = run_query("stores", "--db", self.db)
        self.assertIn("items", batch)

    def test_lineage_points_to_source(self):
        self.build()
        res = run_query("lineage", "--db", self.db, "--table", "f_门店经营指标")
        self.assertEqual(res["count"], 1)
        self.assertEqual(res["items"][0]["source_sha256"], self.src_sha)

    def test_audit_is_readonly_and_lists_pending(self):
        cols = ["门店", "有效订单金额", "某指标_未决", "业务日期", "数据平台", "数据表",
                "采集时间", "渠道口径", "_是否汇总行", "门店标准ID"]
        g = H.group("门店经营指标", "2026-09-30", columns=cols,
                    rows=[["门店A", "1.00", "9", "2026-09-30", "牵牛花", "门店经营指标",
                           "2026-09-30", "全渠道", 0, "S0001"]],
                    business_key=["业务日期", "门店标准ID"])
        DB.apply_batch(self.db, H.prepared("B1", [g]), T)
        before = DB.snapshot_of(self.db)
        res = run_query("audit", "--db", self.db)
        after = DB.snapshot_of(self.db)
        self.assertEqual(before, after)                       # 只读，不改库
        self.assertEqual(res["integrity_check"], "ok")
        self.assertIn(("门店经营指标", "某指标_未决"),
                      [(p["table_name"], p["column_name"]) for p in res["pending_metrics"]])

    def test_pending_metric_returns_no_formal_value(self):
        cols = ["门店", "有效订单金额", "某指标_未决", "业务日期", "数据平台", "数据表",
                "采集时间", "渠道口径", "_是否汇总行", "门店标准ID"]
        g = H.group("f_门店经营指标", "2026-09-30", columns=cols,
                    rows=[["门店A", "1.00", "9", "2026-09-30", "牵牛花", "门店经营指标",
                           "2026-09-30", "全渠道", 0, "S0001"]],
                    business_key=["业务日期", "门店标准ID"])
        DB.apply_batch(self.db, H.prepared("B1", [g]), T)
        res = run_query("get", "--db", self.db, "--table", "f_门店经营指标",
                        "--metric", "某指标_未决")
        self.assertEqual(res["items"][0]["status"], "pending")
        self.assertIsNone(res["items"][0]["value"])           # 待核指标不出正式值

    def test_missing_db_is_clear_error(self):
        res = run_query("audit", "--db", os.path.join(self.tmp, "nope.db"))
        self.assertIn("error", res)


if __name__ == "__main__":
    unittest.main()
