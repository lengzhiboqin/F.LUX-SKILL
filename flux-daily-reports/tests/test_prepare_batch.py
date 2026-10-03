# -*- coding: utf-8 -*-
"""T4：19 表规则、清洗与最终结构（对应 spec S2-1/S2-2）。"""
import json
import os
import shutil
import tempfile
import unittest

import tests
from tests import helpers as H
from clean import load_rules, master_indexes, load_master, prepare_batch, prepare_group
from common import contracts as C
from common import registry as REG
import db as DB
import dedup as D
import normalize as N

REAL_RULES = os.path.join(tests.REFERENCES, "cleaning_rules.json")
REAL_REG = os.path.join(tests.REFERENCES, "table_registry.json")


class TestRulesIntegrity(unittest.TestCase):
    """真实规则与注册表的一致性（19 表全覆盖）。"""

    def test_rules_cover_all_19_registry_tables(self):
        rules = load_rules(REAL_RULES)
        reg = REG.load_registry(REAL_REG)
        self.assertEqual(set(rules["tables"]), set(REG.table_names(reg)))
        self.assertEqual(len(rules["tables"]), 19)

    def test_every_load_to_db_table_has_business_key(self):
        rules = load_rules(REAL_RULES)
        for t, r in rules["tables"].items():
            if r["load_to_db"] and not r["snapshot"]:
                self.assertTrue(r["business_key"], "缺业务键：%s" % t)
            if r["snapshot"]:
                # 滚动快照整表替换，无业务键；靠窗口结束日判新旧
                self.assertEqual(r["business_key"], [])

    def test_excluded_tables_keep_reason(self):
        rules = load_rules(REAL_RULES)
        for t, r in rules["tables"].items():
            if not r["load_to_db"]:
                self.assertTrue(r.get("exclusion_reason"), "排除表缺原因：%s" % t)

    def test_snapshot_table_declared(self):
        rules = load_rules(REAL_RULES)
        self.assertEqual([t for t, r in rules["tables"].items() if r["snapshot"]],
                         ["商品流量_近30天"])

    def test_money_columns_declared_per_table_not_by_keyword(self):
        """`商品毛利率` 这类名字带「金额/率」但不是金额的列不能进 money_cols。"""
        rules = load_rules(REAL_RULES)
        for t, r in rules["tables"].items():
            for c in r["money_cols"]:
                self.assertNotIn("毛利率", c, "%s.%s 被误判为金额列" % (t, c))
                self.assertNotIn("占比", c, "%s.%s 被误判为金额列" % (t, c))


class TestNormalizePrimitives(unittest.TestCase):
    """复用 normalize 的纯函数：编号保真、空值不填 0、单位与量纲。"""

    def test_long_id_kept_exactly(self):
        got = N.parse_number("12345678901234567890")
        self.assertEqual(str(got), "12345678901234567890")   # 不走浮点，末位不丢

    def test_empty_value_is_not_zero(self):
        self.assertIsNone(N.parse_number("-"))
        self.assertIsNone(N.parse_number("--"))
        self.assertEqual(N.parse_cell("-", "某列", "某表"), "")
        self.assertIsNone(N.parse_number("∞"))

    def test_percent_and_pp_are_different_dimensions(self):
        self.assertAlmostEqual(N.parse_percent("8.16%"), 0.0816)
        self.assertAlmostEqual(N.parse_pp("2.10pp"), 2.10)

    def test_label_with_percent_sign_is_not_a_number(self):
        self.assertIsNone(N.parse_percent("top80%商品缺勤损失"))
        self.assertIsNone(N.parse_number("top80%商品缺勤损失"))

    def test_money_rounded_to_cent(self):
        self.assertEqual(str(N.round_money("2353.5899999999997")), "2353.59")

    def test_real_multi_items_are_kept(self):
        """同批次内同一短键的独立明细是真明细，不能合掉。"""
        rows = [
            {"下单时间": "2026-09-01 10:00:00", "订单编号": "A1", "商品SKU码": "S1",
             "商品名称": "苹果", "数量": "1", "采集时间": "2026-09-01"},
            {"下单时间": "2026-09-01 10:00:00", "订单编号": "A1", "商品SKU码": "S1",
             "商品名称": "苹果", "数量": "2", "采集时间": "2026-09-01"},
        ]
        kept, dropped = D.dedup_goods(rows, "商品数据")
        self.assertEqual(len(kept), 2)          # 一单多商品保留
        self.assertEqual(len(dropped), 0)

    def test_earlier_batch_is_dropped(self):
        rows = [
            {"下单时间": "2026-09-01 10:00:00", "订单编号": "A1", "商品SKU码": "S1",
             "商品名称": "苹果", "数量": "1", "采集时间": "2026-09-01"},
            {"下单时间": "2026-09-01 10:00:00", "订单编号": "A1", "商品SKU码": "S1",
             "商品名称": "苹果", "数量": "9", "采集时间": "2026-09-05"},
        ]
        kept, dropped = D.dedup_goods(rows, "商品数据")
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["数量"], "9")   # 只留最新批次
        self.assertEqual(len(dropped), 1)


class TestUnresolvedMetric(unittest.TestCase):
    def test_pending_column_has_no_formal_value(self):
        info = DB.classify_column("某指标_未决")
        self.assertEqual(info["status"], "pending")
        self.assertIsNone(info["formal_source"])

    def test_formal_column_has_source(self):
        info = DB.classify_column("有效订单金额")
        self.assertEqual(info["status"], "formal")
        self.assertEqual(info["unit"], "元")


class TestPrepareBatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="flux_prep_")
        self.cfg = H.config(self.tmp)
        self.master = load_master()
        self.m_idx = master_indexes(self.master)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _manifest(self):
        t1 = H.make_task("门店经营指标", "2026-09-30")
        p = H.qianniu_file(self.tmp, [["1", "示例门店A", "12.30"], ["2", "未登记门店", "5.00"]])
        return H.collect([t1], self.cfg, {"门店经营指标": [p]})

    def test_prepare_produces_groups_with_source_locator(self):
        mf = self._manifest()
        prepared = prepare_batch(mf, H.rules(), self.master)
        self.assertEqual(len(prepared["groups"]), 1)
        g = prepared["groups"][0]
        self.assertEqual(g["status"], C.S_VALIDATED)
        self.assertTrue(g["source_files"][0]["sha256"])
        self.assertIn("业务日期", g["columns"])
        self.assertEqual(g["rows"][0][g["columns"].index("业务日期")], "2026-09-30")

    def test_store_matched_and_unmatched_not_guessed(self):
        mf = self._manifest()
        prepared = prepare_batch(mf, H.rules(), self.master)
        g = prepared["groups"][0]
        ids = [r[g["columns"].index("门店标准ID")] for r in g["rows"]]
        self.assertEqual(ids[0], "S0001")       # 归一店名匹配到主档
        self.assertEqual(ids[1], "")            # 未登记 → 留空，**不猜 ID**

    def test_money_value_normalized_to_cent(self):
        mf = self._manifest()
        prepared = prepare_batch(mf, H.rules(), self.master)
        g = prepared["groups"][0]
        i = g["columns"].index("有效订单金额")
        self.assertEqual(g["rows"][0][i], "12.30")

    def test_incomplete_manifest_is_rejected(self):
        t1 = H.make_task("门店经营指标", "2026-09-30")
        mf = H.collect([t1], self.cfg, {}, statuses={"门店经营指标": C.S_BLOCKED})
        self.assertFalse(C.is_collection_complete(mf))
        with self.assertRaises(ValueError):
            prepare_batch(mf, H.rules(), self.master)

    def test_no_glob_residue_from_directory(self):
        """目录里的无关文件不能被当成输入（只认清单列明的原件）。"""
        mf = self._manifest()
        stray = H.write_csv(os.path.join(self.tmp, "raw", "上游残留_门店经营指标.csv"),
                            ["序号", "门店名称", "有效订单金额"],
                            [["9", "示例门店B", "999.99"]] * 5)
        self.assertTrue(os.path.exists(stray))
        prepared = prepare_batch(mf, H.rules(), self.master)
        g = prepared["groups"][0]
        self.assertEqual(len(g["rows"]), 2)     # 只有清单里的那一个文件的行

    def test_no_data_group_is_replaceable_not_omitted(self):
        t2 = H.make_task("评价数据", "2026-09-29")
        mf = H.collect([t2], self.cfg, {}, statuses={"评价数据": C.S_NO_DATA},
                       evidence={"评价数据": {"platform_task_id": "P-1",
                                             "no_data_response": "该日期无评价数据"}})
        prepared = prepare_batch(mf, H.rules(), self.master)
        self.assertEqual(len(prepared["groups"]), 1)   # 空组也在，代表「可替换的修订」
        self.assertEqual(prepared["groups"][0]["status"], C.S_NO_DATA)
        self.assertEqual(prepared["groups"][0]["rows"], [])

    def test_source_file_missing_before_clean_is_rejected(self):
        mf = self._manifest()
        p = mf["results"][0]["files"][0]["path"]
        os.remove(p)
        with self.assertRaises(ValueError):
            prepare_batch(mf, H.rules(), self.master)


if __name__ == "__main__":
    unittest.main()
