# -*- coding: utf-8 -*-
"""自检器归一化口径的回归测试（2026-10-01 实机核对后固化）。

覆盖三类真实踩过的坑：
  1. 「率」列三种写法（带 `(%)` / 名带「率」带 `%` / 名带「率」裸比率）
  2. 金额列分位舍入（原库 2 位）
  3. 去重表丢行归 `SOURCE_DEDUPED`（非缺陷）而非 `SOURCE_ROWS_MISSING_IN_DB`
  4. 抽样确定性（固定 seed 可复现）且抽样不判丢行
"""
import unittest

from selfcheck import (_cmp_value, _is_pct_col, _norm_money, _norm_rate,
                       _deterministic_sample, NON_DEFECT_CODES)


class TestRatioColumns(unittest.TestCase):
    def test_pct_suffix_col_detected(self):
        for name in ("订单商品点踩率(%)", "订单商品点踩率（%）",
                     "转化率(％)", "xx率%）"):
            self.assertTrue(_is_pct_col(name), name)

    def test_plain_rate_col_not_pct(self):
        for name in ("缺货退款率", "商品毛利率", "动销率"):
            self.assertFalse(_is_pct_col(name), name)

    def test_pct_col_source_divides_bare_number(self):
        # 列头声明 (%)：源件裸数字 `100` 是百分数 → 库内比率 `1`
        self.assertEqual(_cmp_value("100", "订单商品点踩率(%)", "source"), "1")
        self.assertEqual(_cmp_value("100", "订单商品点踩率(%)", "db"), "100")

    def test_pct_col_side_asymmetry(self):
        # 同一列两侧取向不同：源件 100 → 1；库内 1 → 1（库内已是比率，不重复除）
        self.assertEqual(_cmp_value("100.00", "订单商品点踩率(%)", "source"), "1")
        self.assertEqual(_cmp_value("1", "订单商品点踩率(%)", "db"), "1")
        self.assertEqual(_cmp_value("0.75", "订单商品点踩率(%)", "source"), "0.0075")
        self.assertEqual(_cmp_value("0.75", "订单商品点踩率(%)", "db"), "0.75")

    def test_rate_col_with_percent_sign_divides(self):
        # 名带「率」无括号：源件带 `%` 才换算（4.17% → 0.04，2 位分位）
        self.assertEqual(_cmp_value("4.17%", "缺货退款率", "source"), "0.04")
        self.assertEqual(_cmp_value("0.04", "缺货退款率", "db"), "0.04")

    def test_rate_col_bare_is_already_ratio(self):
        # 名带「率」无括号：裸数字已是比率，只舍 2 位（0.7848… → 0.78）
        self.assertEqual(_cmp_value("0.7848605577689243", "商品毛利率", "source"), "0.78")
        self.assertEqual(_cmp_value("0.78", "商品毛利率", "db"), "0.78")

    def test_rate_col_both_sides_agree(self):
        cases = [("缺货退款率", "4.17%", "0.04"),
                 ("缺货退款率", "0.0417", "0.04"),
                 ("商品毛利率", "0.7848605577689243", "0.78"),
                 ("商品毛利率", "78.49%", "0.78")]
        for col, src, db in cases:
            self.assertEqual(_cmp_value(src, col, "source"),
                             _cmp_value(db, col, "db"), (col, src, db))


class TestMoneyRounding(unittest.TestCase):
    def test_half_up_two_decimals(self):
        self.assertEqual(_norm_money("5969.999529999999"), "5970")
        self.assertEqual(_norm_money("42.12952999999999"), "42.13")

    def test_int_drops_decimal(self):
        self.assertEqual(_norm_money("100.00"), "100")

    def test_money_col_detection(self):
        self.assertEqual(_cmp_value("42.12952999999999", "商品毛利额", "source"), "42.13")
        self.assertEqual(_cmp_value("42.13", "商品毛利额", "db"), "42.13")


class TestNonDefectCodes(unittest.TestCase):
    def test_both_codes_are_non_defect(self):
        self.assertIn("LEGACY_BLANKED", NON_DEFECT_CODES)
        self.assertIn("SOURCE_DEDUPED", NON_DEFECT_CODES)

    def test_missing_row_code_is_defect(self):
        # 丢行本码是缺陷（与去重码区分）
        self.assertNotIn("SOURCE_ROWS_MISSING_IN_DB", NON_DEFECT_CODES)


class TestDeterministicSample(unittest.TestCase):
    def _rows(self):
        return [{"业务日期": "2026-09-29", "店铺ID": str(i), "订单商品": "P%d" % i,
                 "_scope_id": "all"} for i in range(1, 61)]

    def test_same_seed_same_sample(self):
        a = _deterministic_sample(self._rows(), 10, 7)
        b = _deterministic_sample(self._rows(), 10, 7)
        self.assertEqual(a, b)
        self.assertEqual(len(a), 10)

    def test_different_seed_different_sample(self):
        a = _deterministic_sample(self._rows(), 10, 7)
        b = _deterministic_sample(self._rows(), 10, 8)
        self.assertNotEqual([r["店铺ID"] for r in a], [r["店铺ID"] for r in b])

    def test_sample_is_subset(self):
        rows = self._rows()
        s = _deterministic_sample(rows, 5, 1)
        self.assertTrue(all(r in rows for r in s))

    def test_no_sampling_when_under_size(self):
        rows = self._rows()[:3]
        self.assertEqual(len(_deterministic_sample(rows, 10, 1)), 3)


if __name__ == "__main__":
    unittest.main()
