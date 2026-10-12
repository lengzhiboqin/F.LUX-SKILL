# -*- coding: utf-8 -*-
"""T6：独立自检（对应 spec S2-4）。"""
import os
import shutil
import sqlite3
import tempfile
import unittest

import tests
from tests import helpers as H
from clean import load_master, master_indexes, prepare_batch
from selfcheck import check_database, force_passed
from common import contracts as C
import db as DB

T = "2026-10-01"
QCOLS = ["门店", "有效订单金额", "业务日期", "数据平台", "数据表", "采集时间",
         "渠道口径", "_是否汇总行", "门店标准ID"]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="flux_check_")
        self.db = os.path.join(self.tmp, "flux.db")
        self.cfg = H.config(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self, rows=None, scope_id="all", date="2026-09-30"):
        rows = rows or [["1", "示例门店A", "12.30"], ["2", "示例门店B", "5.00"]]
        compact = date.replace("-", "")
        name = "门店经营指标_%s.csv" if scope_id == "all" else "门店经营指标_%s_ch100.csv"
        p = H.qianniu_file(self.tmp, rows, date=compact, name=name)
        task = H.make_task("门店经营指标", date, scope_id=scope_id)
        mf = H.collect([task], self.cfg, {"门店经营指标": [p]})
        prepared = prepare_batch(mf, H.rules(), load_master())
        return DB.apply_batch(self.db, prepared, T), p, prepared


class TestValueCheck(Base):
    def test_clean_database_passes(self):
        self.build()
        res = check_database(self.db, {})
        self.assertEqual(res["issues"], [])
        self.assertTrue(res["passed"])
        self.assertGreaterEqual(res["checked_rows"], 1)

    def test_corrupted_amount_is_detected(self):
        self.build()
        con = sqlite3.connect(self.db)
        con.execute('UPDATE "f_门店经营指标" SET "有效订单金额"=? WHERE "门店标准ID"=?',
                    ("999.99", "S0001"))
        con.commit()
        con.close()
        res = check_database(self.db, {})
        codes = {i["code"] for i in res["issues"]}
        self.assertFalse(res["passed"])
        self.assertIn("VALUE_MISMATCH", codes)
        self.assertFalse(force_passed(res))

    def test_readonly_check_never_mutates(self):
        self.build()
        before = DB.snapshot_of(self.db)
        res = check_database(self.db, {})
        after = DB.snapshot_of(self.db)
        self.assertEqual(before, after)
        self.assertEqual(res["db_fingerprint_before"], res["db_fingerprint_after"])
        self.assertNotIn("DB_MUTATED_BY_CHECK", {i["code"] for i in res["issues"]})

    def test_missing_source_is_reported_not_passed(self):
        _, p, _ = self.build()
        os.remove(p)
        res = check_database(self.db, {})
        self.assertTrue(res["source_unavailable"])
        self.assertGreaterEqual(res["checked_rows"], 0)

    def test_filtered_date_range(self):
        self.build(rows=[["1", "示例门店A", "1.00"]], date="2026-09-29")
        self.build(rows=[["1", "示例门店A", "2.00"]], date="2026-09-30")
        res = check_database(self.db, {"start": "2026-09-30", "end": "2026-09-30"})
        self.assertEqual(res["request"]["start"], "2026-09-30")
        self.assertEqual(res["per_table"]["f_门店经营指标"]["rows_in_db"], 1)

    def test_invalid_request_date_rejected(self):
        with self.assertRaises(ValueError):
            check_database(self.db, {"start": "2026-02-30"})
        self.build()
        with self.assertRaises(ValueError):
            check_database(self.db, {"mode": "guess"})


class TestSampling(Base):
    def test_sample_size_respected_and_seeded(self):
        rows = [["%d" % i, "商品%d" % i] for i in range(1, 61)]
        p = H.shangou_file(self.tmp, [["2026-09-29", str(r[0]), r[1]] for r in rows])
        task = H.make_task("评价数据", "2026-09-29")
        mf = H.collect([task], self.cfg, {"评价数据": [p]})
        prepared = prepare_batch(mf, H.rules(), load_master())
        DB.apply_batch(self.db, prepared, T)

        full = check_database(self.db, {"tables": ["f_评价数据"]})
        self.assertEqual(full["checked_rows"], 60)

        sample = check_database(self.db, {"tables": ["f_评价数据"], "mode": "sample",
                                          "sample_size": 10, "seed": 7})
        self.assertEqual(sample["request"]["mode"], "sample")
        self.assertEqual(sample["checked_rows"], 10)
        self.assertEqual(sample["per_table"]["f_评价数据"]["checked"], 10)
        # 同一 seed 可复现
        again = check_database(self.db, {"tables": ["f_评价数据"], "mode": "sample",
                                         "sample_size": 10, "seed": 7})
        self.assertEqual(again["checked_rows"], sample["checked_rows"])

    def test_corruption_inside_sample_is_caught(self):
        rows = [["%d" % i, "商品%d" % i] for i in range(1, 61)]
        p = H.shangou_file(self.tmp, [["2026-09-29", str(r[0]), r[1]] for r in rows])
        task = H.make_task("评价数据", "2026-09-29")
        mf = H.collect([task], self.cfg, {"评价数据": [p]})
        DB.apply_batch(self.db, prepare_batch(mf, H.rules(), load_master()), T)
        con = sqlite3.connect(self.db)
        con.execute('UPDATE "f_评价数据" SET "订单商品"=? WHERE "店铺ID"=?', ("改坏了", "1"))
        con.commit()
        con.close()
        res = check_database(self.db, {"tables": ["f_评价数据"]})
        self.assertIn("VALUE_MISMATCH", {i["code"] for i in res["issues"]})


class TestSchemaAndCoverage(Base):
    def test_missing_coverage_reported(self):
        con = sqlite3.connect(self.db)
        DB.ensure_meta_tables(con)
        con.execute('CREATE TABLE "f_门店经营指标" (_行序号 INTEGER, "业务日期" TEXT, '
                    '_scope_id TEXT, _batch_id TEXT)')
        con.commit()
        con.close()
        res = check_database(self.db, {"tables": ["f_门店经营指标"]})
        self.assertIn("COVERAGE_MISSING", {i["code"] for i in res["issues"]})

    def test_expired_rows_are_reported_not_deleted(self):
        con = sqlite3.connect(self.db)
        con.execute('CREATE TABLE "f_门店经营指标" (_行序号 INTEGER, "业务日期" TEXT, '
                    '"门店标准ID" TEXT, _scope_id TEXT, _batch_id TEXT)')
        con.execute('INSERT INTO "f_门店经营指标" VALUES (1,"2020-01-01","S1","all","B0")')
        con.commit()
        con.close()
        res = check_database(self.db, {"tables": ["f_门店经营指标"]})
        self.assertGreaterEqual(res["expired_rows_reported"], 1)
        con = sqlite3.connect(self.db)
        n = con.execute('SELECT COUNT(*) FROM "f_门店经营指标"').fetchone()[0]
        con.close()
        self.assertEqual(n, 1)      # 只报告，不偷偷清理


class TestRowOrderIndependence(Base):
    """回归：行序不能影响结论。

    曾出现的真实缺陷：按**位置**配对库行与源行，而 SQLite 的行序取决于查询计划
    （全表扫描=插入序、带日期条件走主键索引=字典序）。同一份数据「全量通过、
    带范围误报」。这里用「主键顺序 ≠ 文件顺序」的数据把两种读法都跑一遍。
    """

    def _seed(self):
        # 店铺ID 用 1..60：字典序（1,10,11,…,2,20,…）≠ 文件顺序（1,2,3,…）
        rows = [["%d" % i, "商品%d" % i] for i in range(1, 61)]
        p = H.shangou_file(self.tmp, [["2026-09-29", r[0], r[1]] for r in rows])
        task = H.make_task("评价数据", "2026-09-29")
        mf = H.collect([task], self.cfg, {"评价数据": [p]})
        DB.apply_batch(self.db, prepare_batch(mf, H.rules(), load_master()), T)

    def test_full_and_filtered_agree(self):
        self._seed()
        full = check_database(self.db, {"tables": ["f_评价数据"]})
        rng = check_database(self.db, {"tables": ["f_评价数据"],
                                       "start": "2026-09-29", "end": "2026-09-30"})
        self.assertEqual(full["issues"], [])
        self.assertEqual(rng["issues"], [])
        self.assertTrue(full["passed"] and rng["passed"])
        self.assertEqual(full["checked_rows"], rng["checked_rows"])

    def test_fabricated_row_is_still_caught(self):
        """顺序无关后不能把「无中生有」放过。"""
        self._seed()
        con = sqlite3.connect(self.db)
        con.execute('INSERT INTO "f_评价数据" ("_行序号","店铺ID","订单商品","业务日期",'
                    '"_scope_id","_batch_id") VALUES (99,?,?,?,?,?)',
                    ("999", "凭空捏造", "2026-09-29", "all", "B-x"))
        con.commit()
        con.close()
        res = check_database(self.db, {"tables": ["f_评价数据"]})
        codes = {i["code"] for i in res["issues"]}
        self.assertIn("VALUE_MISMATCH", codes)
        self.assertFalse(res["passed"])

    def test_lost_row_is_caught(self):
        self._seed()
        con = sqlite3.connect(self.db)
        con.execute('DELETE FROM "f_评价数据" WHERE "店铺ID"=?', ("30",))
        con.commit()
        con.close()
        res = check_database(self.db, {"tables": ["f_评价数据"]})
        # 评价数据非去重表：源件有行而库内没有 → 丢行缺陷（旧码 ROW_COUNT_MISMATCH 已并入此码）
        self.assertIn("SOURCE_ROWS_MISSING_IN_DB", {i["code"] for i in res["issues"]})
        self.assertFalse(res["passed"])


if __name__ == "__main__":
    unittest.main()
