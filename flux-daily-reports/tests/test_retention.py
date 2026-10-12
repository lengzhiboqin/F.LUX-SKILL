# -*- coding: utf-8 -*-
"""365 日保留、独立维护与备份恢复（对应 spec S2-5）。"""
import os
import shutil
import sqlite3
import tempfile
import unittest

import tests
from tests import helpers as H
from common import contracts as C
from common import dates
import db as DB

T = "2026-10-01"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="flux_ret_")
        self.db = os.path.join(self.tmp, "flux.db")
        self.bak = os.path.join(self.tmp, "backup")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_fact(self, dates_list, table="门店经营指标"):
        con = sqlite3.connect(self.db)
        try:
            con.execute(DB.fact_ddl(table, ["门店", "业务日期", "数据平台", "数据表",
                                            "采集时间", "渠道口径", "_是否汇总行",
                                            "门店标准ID"], ["业务日期", "门店标准ID"]))
            cols = [DB.SEQ_COL] + ["门店", "业务日期", "数据平台", "数据表", "采集时间",
                                   "渠道口径", "_是否汇总行", "门店标准ID",
                                   DB.SCOPE_COL, DB.BATCH_COL]
            sql = 'INSERT INTO "%s" (%s) VALUES (%s)' % (
                table, ", ".join('"%s"' % c for c in cols), ", ".join("?" * len(cols)))
            payload = [[i + 1, "门店1", d, "牵牛花", table, d, "全渠道", 0, "S0001", "all", "B0"]
                       for i, d in enumerate(dates_list)]
            con.executemany(sql, payload)
            con.commit()
        finally:
            con.close()

    def dates_in(self, table="门店经营指标"):
        con = DB.open_readonly(self.db)
        try:
            return sorted(r[0] for r in con.execute(
                'SELECT DISTINCT "业务日期" FROM "%s"' % table))
        finally:
            con.close()


class TestRetentionWindow(Base):
    def test_window_is_Tm365_to_Tm1(self):
        start, end = DB.retention_window(T)
        self.assertEqual((start, end), ("2025-10-01", "2026-09-30"))
        self.assertEqual(len(dates.window_dates(T, 365, 1)), 365)

    def test_leap_year_window_still_365_days(self):
        t = "2028-03-01"
        start, end = DB.retention_window(t)
        self.assertEqual(len(dates.window_dates(t, 365, 1)), 365)
        self.assertEqual((start, end), (dates.shift_iso(t, -365), dates.shift_iso(t, -1)))

    def test_purge_cuts_before_cutoff_and_keeps_boundary(self):
        keep = dates.window_dates(T, 365, 1)          # 2025-10-01 ~ 2026-09-30
        self.make_fact(keep + ["2025-09-30"])         # 多一行过期
        con = sqlite3.connect(self.db)
        try:
            res = DB.purge_expired(con, T)
            con.commit()
        finally:
            con.close()
        self.assertEqual(res["expired_rows"], 1)
        got = self.dates_in()
        self.assertEqual(len(got), 365)
        self.assertEqual(got[0], "2025-10-01")
        self.assertEqual(got[-1], "2026-09-30")

    def test_purge_does_not_touch_non_date_meta(self):
        con = sqlite3.connect(self.db)
        try:
            DB.ensure_meta_tables(con)
            con.execute("INSERT OR REPLACE INTO _store_master (store_id, name) VALUES (?,?)",
                        ("S0001", "示例门店A"))
            con.execute("INSERT OR REPLACE INTO _catalog (table_name, column_name, scope_id,"
                        " status) VALUES (?,?,?,?)",
                        ("门店经营指标", "有效订单金额", "all", "formal"))
            con.commit()
        finally:
            con.close()
        self.make_fact(["2020-01-01"])
        con = sqlite3.connect(self.db)
        try:
            DB.purge_expired(con, T)
            con.commit()
            self.assertEqual(con.execute("SELECT COUNT(*) FROM _store_master").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM _catalog").fetchone()[0], 1)
        finally:
            con.close()

    def test_purge_dry_run_deletes_nothing(self):
        self.make_fact(["2020-01-01", "2026-09-30"])
        con = sqlite3.connect(self.db)
        try:
            res = DB.purge_expired(con, T, dry_run=True)
            con.rollback()
        finally:
            con.close()
        self.assertEqual(res["expired_rows"], 1)
        self.assertEqual(len(self.dates_in()), 2)      # 一行没删


class TestMaintenance(Base):
    def _seed(self, n_old=1):
        keep = dates.window_dates(T, 365, 1)
        self.make_fact(keep + ["2020-01-%02d" % i for i in range(1, n_old + 1)])

    def test_maintain_without_insert_backs_up_then_deletes(self):
        self._seed(n_old=2)
        before = DB.snapshot_of(self.db)
        res = DB.maintain_retention(self.db, T, backup_dir=self.bak, compact=False)
        self.assertTrue(os.path.exists(res["backup"]["path"]))
        self.assertEqual(res["backup"]["bytes"], os.path.getsize(res["backup"]["path"]))
        self.assertTrue(res["backup"]["sha256"])
        self.assertEqual(res["deleted"], 2)
        after = DB.snapshot_of(self.db)
        self.assertEqual(after["门店经营指标"], before["门店经营指标"] - 2)

    def test_dry_run_keeps_data_and_makes_no_backup(self):
        self._seed(n_old=2)
        res = DB.maintain_retention(self.db, T, backup_dir=self.bak, dry_run=True)
        self.assertEqual(res["deleted"], 0)
        self.assertIsNone(res["backup"])
        self.assertEqual(res["preview"]["expired_rows"], 2)

    def test_compaction_reports_truthfully_when_nothing_to_reclaim(self):
        con = sqlite3.connect(self.db)
        con.execute("CREATE TABLE x (a TEXT)")
        con.commit()
        con.close()
        res = DB.maintain_retention(self.db, T, backup_dir=self.bak, compact=True)
        # 没有过期数据、没有可回收页 → 不伪报已释放空间
        self.assertFalse(res["compaction"]["space_released"])
        self.assertFalse(res["compaction"]["executed"])

    def test_backup_is_consistent_and_restorable(self):
        self._seed(n_old=0)
        copy = os.path.join(self.tmp, "restore.db")
        info = DB.backup_database(self.db, copy)
        self.assertEqual(DB.snapshot_of(copy), DB.snapshot_of(self.db))
        con = sqlite3.connect(copy)
        try:
            self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            con.close()
        import hashlib
        with open(copy, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        self.assertEqual(info["sha256"], digest)

    def test_snapshot_rows_not_cut_by_retention(self):
        cols = ["商品名称", "业务日期", "数据平台", "数据表", "采集时间", "渠道口径",
                "_是否汇总行", "门店标准ID"]
        rows = [["商品%d" % i, "2026-09-30", "牵牛花", "商品流量_近30天", "2026-09-30",
                 "全渠道", 0, ""] for i in range(5)]
        g = H.group("商品流量_近30天", "2026-09-30", columns=cols, rows=rows,
                    business_key=[], snapshot=True, window_start="2026-09-01",
                    window_end="2026-09-30")
        DB.apply_batch(self.db, H.prepared("B1", [g]), T)
        DB.maintain_retention(self.db, T, backup_dir=self.bak)
        con = DB.open_readonly(self.db)
        try:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM "商品流量_近30天"').fetchone()[0], 5)
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()
