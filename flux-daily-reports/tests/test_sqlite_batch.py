# -*- coding: utf-8 -*-
"""T5：单事务入库、修订与回滚（对应 spec S2-3/S2-5）。"""
import os
import shutil
import sqlite3
import tempfile
import unittest

import tests
from tests import helpers as H
from common import contracts as C
import db as DB

COLS = ["门店", "有效订单金额", "业务日期", "数据平台", "数据表", "采集时间",
        "渠道口径", "_是否汇总行", "门店标准ID"]
KEYS = ["业务日期", "门店标准ID"]


def row(store, amount, date="2026-09-30"):
    return ["门店" + store, amount, date, "牵牛花", "门店经营指标", date, "全渠道", 0, "S000" + store]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="flux_db_")
        self.db = os.path.join(self.tmp, "flux.db")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def count(self, table):
        con = sqlite3.connect(self.db)
        try:
            return con.execute('SELECT COUNT(*) FROM "%s"' % table).fetchone()[0]
        finally:
            con.close()


class TestInsertReplayRevision(Base):
    def test_insert_replay_and_revision(self):
        g = H.group("门店经营指标", "2026-09-30", columns=COLS,
                    rows=[row("1", "10.00"), row("2", "20.00")], business_key=KEYS)
        r1 = DB.apply_batch(self.db, H.prepared("B1", [g]), "2026-10-01")
        self.assertEqual(r1["inserted"], 2)
        self.assertEqual(self.count("门店经营指标"), 2)

        # 同输入重跑 → 不重复计数
        DB.apply_batch(self.db, H.prepared("B1-replay", [g]), "2026-10-01")
        self.assertEqual(self.count("门店经营指标"), 2)

        # 受控修订：同业务键金额改了 → 替换而不是新增
        g2 = H.group("门店经营指标", "2026-09-30", columns=COLS,
                     rows=[row("1", "99.00"), row("2", "20.00")], business_key=KEYS)
        DB.apply_batch(self.db, H.prepared("B2", [g2]), "2026-10-01")
        self.assertEqual(self.count("门店经营指标"), 2)
        con = sqlite3.connect(self.db)
        got = con.execute('SELECT "有效订单金额" FROM "门店经营指标" WHERE "门店标准ID"=?',
                          ("S0001",)).fetchone()[0]
        con.close()
        self.assertEqual(got, "99.00")

    def test_revision_shrinks_group(self):
        g = H.group("门店经营指标", "2026-09-30", columns=COLS,
                    rows=[row("1", "10"), row("2", "20"), row("3", "30")], business_key=KEYS)
        DB.apply_batch(self.db, H.prepared("B1", [g]), "2026-10-01")
        self.assertEqual(self.count("门店经营指标"), 3)
        # 源端整组减少：只剩 1 行同键 → 旧组被整组替换，不残留
        g2 = H.group("门店经营指标", "2026-09-30", columns=COLS,
                     rows=[row("1", "10")], business_key=KEYS)
        DB.apply_batch(self.db, H.prepared("B2", [g2]), "2026-10-01")
        self.assertEqual(self.count("门店经营指标"), 1)

    def test_revision_becomes_no_data_deletes_old_group(self):
        g = H.group("门店经营指标", "2026-09-30", columns=COLS,
                    rows=[row("1", "10"), row("2", "20")], business_key=KEYS)
        DB.apply_batch(self.db, H.prepared("B1", [g]), "2026-10-01")
        empty = H.group("门店经营指标", "2026-09-30", columns=COLS, rows=[],
                        business_key=KEYS, status=C.S_NO_DATA)
        DB.apply_batch(self.db, H.prepared("B2", [empty]), "2026-10-01")
        self.assertEqual(self.count("门店经营指标"), 0)
        con = sqlite3.connect(self.db)
        st = con.execute("SELECT status FROM _coverage WHERE table_name=?",
                         ("门店经营指标",)).fetchone()[0]
        con.close()
        self.assertEqual(st, C.S_NO_DATA)

    def test_real_multi_item_kept_with_seq(self):
        rows = [row("1", "10"), row("1", "10")]     # 同一业务键两行 = 合法重复明细
        g = H.group("门店经营指标", "2026-09-30", columns=COLS, rows=rows, business_key=KEYS)
        DB.apply_batch(self.db, H.prepared("B1", [g]), "2026-10-01")
        self.assertEqual(self.count("门店经营指标"), 2)


class TestScopeIsolation(Base):
    def test_same_date_different_scope_are_independent(self):
        g1 = H.group("门店经营指标", "2026-09-30", scope_id="channel:100", columns=COLS,
                     rows=[row("1", "10")], business_key=KEYS)
        g2 = H.group("门店经营指标", "2026-09-30", scope_id="channel:200", columns=COLS,
                     rows=[row("1", "77"), row("2", "20")], business_key=KEYS)
        DB.apply_batch(self.db, H.prepared("B1", [g1, g2]), "2026-10-01")
        self.assertEqual(self.count("门店经营指标"), 3)
        con = sqlite3.connect(self.db)
        got = dict(con.execute('SELECT _scope_id, COUNT(*) FROM "门店经营指标" GROUP BY _scope_id'))
        con.close()
        self.assertEqual(got, {"channel:100": 1, "channel:200": 2})


class TestRollback(Base):
    def test_one_bad_group_rolls_back_whole_batch(self):
        good = H.group("门店经营指标", "2026-09-30", columns=COLS,
                       rows=[row("1", "10")], business_key=KEYS)
        DB.apply_batch(self.db, H.prepared("B1", [good]), "2026-10-01")
        before = DB.snapshot_of(self.db)

        # 第二张表先建好，再用「多出一列」触发结构变化
        cols_b = ["门店", "业务日期", "数据平台", "数据表", "采集时间", "渠道口径",
                  "_是否汇总行", "门店标准ID"]
        b1 = H.group("品类分析", "2026-09-30", columns=cols_b,
                     rows=[["门店1", "2026-09-30", "牵牛花", "品类分析", "2026-09-30",
                            "全渠道", 0, "S0001"]], business_key=["业务日期", "门店标准ID"])
        DB.apply_batch(self.db, H.prepared("B2", [b1]), "2026-10-01")

        mid = DB.snapshot_of(self.db)
        new_good = H.group("门店经营指标", "2026-09-29", columns=COLS,
                           rows=[row("2", "55", "2026-09-29")], business_key=KEYS)
        bad = H.group("品类分析", "2026-09-30", columns=cols_b + ["新出现的列"],
                      rows=[["门店1", "2026-09-30", "牵牛花", "品类分析", "2026-09-30",
                             "全渠道", 0, "S0001", "x"]],
                      business_key=["业务日期", "门店标准ID"])
        with self.assertRaises(ValueError):
            DB.apply_batch(self.db, H.prepared("B3", [new_good, bad]), "2026-10-01")
        # 整批回滚：新的那张表数据没有进去
        self.assertEqual(DB.snapshot_of(self.db), mid)

    def test_empty_key_value_rejected_before_transaction(self):
        bad = H.group("门店经营指标", "2026-09-30", columns=COLS,
                      rows=[["门店1", "10", "2026-09-30", "牵牛花", "门店经营指标",
                             "2026-09-30", "全渠道", 0, ""]],   # 门店标准ID 为空
                      business_key=KEYS)
        with self.assertRaises(ValueError):
            DB.apply_batch(self.db, H.prepared("B1", [bad]), "2026-10-01")
        self.assertFalse(os.path.exists(self.db))


class TestCommitAuthority(Base):
    def test_committed_batch_is_recorded_and_queryable(self):
        g = H.group("门店经营指标", "2026-09-30", columns=COLS,
                    rows=[row("1", "10")], business_key=KEYS)
        res = DB.apply_batch(self.db, H.prepared("B-COMMIT-1", [g]), "2026-10-01")
        con = sqlite3.connect(self.db)
        got = con.execute("SELECT status, inserted FROM _batch WHERE batch_id=?",
                          ("B-COMMIT-1",)).fetchone()
        con.close()
        self.assertEqual(got, ("committed", 1))
        self.assertEqual(res["committed_version"], "B-COMMIT-1")

    def test_coverage_and_lineage_written_in_same_transaction(self):
        g = H.group("门店经营指标", "2026-09-30", columns=COLS,
                    rows=[row("1", "10")], business_key=KEYS)
        DB.apply_batch(self.db, H.prepared("B1", [g]), "2026-10-01")
        con = sqlite3.connect(self.db)
        cov = con.execute("SELECT status, rows FROM _coverage WHERE table_name=?",
                          ("门店经营指标",)).fetchone()
        lin = con.execute("SELECT source_sha256, row_count FROM _lineage WHERE table_name=?",
                          ("门店经营指标",)).fetchone()
        cat = con.execute("SELECT COUNT(*) FROM _catalog WHERE table_name=?",
                          ("门店经营指标",)).fetchone()[0]
        con.close()
        self.assertEqual(cov, (C.S_VALIDATED, 1))
        self.assertEqual(lin[1], 1)
        self.assertGreater(cat, 0)

    def test_excluded_table_recorded_but_not_loaded(self):
        g = H.group("门店流量排行", "2026-09-30", columns=["门店", "业务日期"],
                    rows=[["门店1", "2026-09-30"]], business_key=[], load_to_db=False)
        DB.apply_batch(self.db, H.prepared("B1", [g]), "2026-10-01")
        con = sqlite3.connect(self.db)
        n = con.execute("SELECT COUNT(*) FROM _coverage WHERE table_name=?",
                        ("门店流量排行",)).fetchone()[0]
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        con.close()
        self.assertEqual(n, 1)
        self.assertNotIn("门店流量排行", tables)     # 不入库，但留了覆盖记录


class TestSnapshot(Base):
    def test_snapshot_whole_replaced_never_truncated(self):
        cols = ["商品名称", "业务日期", "数据平台", "数据表", "采集时间", "渠道口径",
                "_是否汇总行", "门店标准ID"]
        rows = [["商品%d" % i, "2026-09-30", "牵牛花", "商品流量_近30天", "2026-09-30",
                 "全渠道", 0, ""] for i in range(3)]
        g = H.group("商品流量_近30天", "2026-09-30", columns=cols, rows=rows,
                    business_key=[], snapshot=True, window_start="2026-09-01",
                    window_end="2026-09-30")
        DB.apply_batch(self.db, H.prepared("B1", [g]), "2026-10-01")
        self.assertEqual(self.count("商品流量_近30天"), 3)   # 内容完整，不裁切

    def test_older_snapshot_rejected(self):
        cols = ["商品名称", "业务日期", "数据平台", "数据表", "采集时间", "渠道口径",
                "_是否汇总行", "门店标准ID"]
        mk = lambda end: H.group(                       # noqa: E731
            "商品流量_近30天", end, columns=cols,
            rows=[["商品1", end, "牵牛花", "商品流量_近30天", end, "全渠道", 0, ""]],
            business_key=[], snapshot=True, window_start="2026-08-01", window_end=end)
        DB.apply_batch(self.db, H.prepared("B1", [mk("2026-09-30")]), "2026-10-01")
        with self.assertRaises(ValueError):
            DB.apply_batch(self.db, H.prepared("B2", [mk("2026-08-31")]), "2026-10-01")
        self.assertEqual(self.count("商品流量_近30天"), 1)


if __name__ == "__main__":
    unittest.main()
