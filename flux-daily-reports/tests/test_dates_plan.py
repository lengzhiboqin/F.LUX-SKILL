# -*- coding: utf-8 -*-
"""T1：日期、七日缺口与批次身份（对应 spec S1-1）。"""
import os
import unittest

import tests  # noqa: F401 —— 触发 sys.path 引导
from common import contracts as C
from common import dates
from common import registry as REG
from run_daily import check_gap, plan_daily, plan_targeted

REG_PATH = os.path.join(tests.REFERENCES, "table_registry.json")
T = "2026-10-01"


def reg():
    return REG.load_registry(REG_PATH)


def target_keys(t, r):
    """19 张表的「目标日任务」键（普通表 T-1、评价 T-2、快照为快照窗口）。"""
    keys = set()
    sw = r.get("snapshot_window", {"days": 30, "offset": 1})
    for e in r["tables"]:
        if e["type"] == "snapshot":
            ws, we = dates.snapshot_window(t, sw["days"], sw["offset"])
            keys.add(C.task_key(C.make_task(e["platform"], e["table"], we,
                                            window_start=ws, window_end=we)))
        else:
            d = dates.shift_iso(t, -e["default_offset_days"])
            keys.add(C.task_key(C.make_task(e["platform"], e["table"], d)))
    return keys


def ledger_complete_all_but(t, r, keep_missing=()):
    """构造一个「窗口内全部已完成、只有 keep_missing 是缺口」的台账。"""
    plan = plan_daily(t, r, {})
    led = {}
    for task in plan["tasks"]:
        if task["task_key"] in keep_missing:
            continue
        C.ledger_update(led, {"task_key": task["task_key"], "status": C.S_VALIDATED,
                              "platform": task["platform"], "table": task["table"],
                              "business_date": task["business_date"],
                              "window_start": task["window_start"],
                              "window_end": task["window_end"],
                              "scope_id": task["scope_id"], "collected_at": t, "files": []})
    return led


class TestWindows(unittest.TestCase):
    def test_normal_window_is_Tm7_Tm1(self):
        normal_dates = dates.window_dates(T, 7, 1)
        self.assertEqual(normal_dates, ["2026-09-24", "2026-09-25", "2026-09-26",
                                        "2026-09-27", "2026-09-28", "2026-09-29",
                                        "2026-09-30"])
        self.assertEqual(len(normal_dates), 7)

    def test_review_window_is_Tm8_Tm2(self):
        review_dates = dates.window_dates(T, 7, 2)
        self.assertEqual(review_dates[0], "2026-09-23")
        self.assertEqual(review_dates[-1], "2026-09-29")
        self.assertEqual(len(review_dates), 7)

    def test_snapshot_window_is_Tm30_Tm1(self):
        ws, we = dates.snapshot_window(T, 30, 1)
        self.assertEqual((ws, we), ("2026-09-01", "2026-09-30"))

    def test_shift_across_month_and_year(self):
        self.assertEqual(dates.shift_iso("2026-03-01", -1), "2026-02-28")
        self.assertEqual(dates.shift_iso("2026-01-01", -1), "2025-12-31")

    def test_leap_year(self):
        self.assertTrue(dates.is_valid_iso("2024-02-29"))
        self.assertFalse(dates.is_valid_iso("2026-02-29"))


class TestRegistry(unittest.TestCase):
    def test_registry_is_19_tables_two_platforms(self):
        r = reg()
        REG.check_complete(r)
        self.assertEqual(len(r["tables"]), 19)
        self.assertEqual(len(REG.tables_of(r, "牵牛花")), 11)
        self.assertEqual(len(REG.tables_of(r, "闪购")), 8)

    def test_snapshot_and_event_types(self):
        r = reg()
        self.assertEqual(REG.get_table(r, "商品流量_近30天")["type"], "snapshot")
        self.assertEqual(REG.get_table(r, "评价数据")["type"], "event")
        self.assertEqual(REG.get_table(r, "评价数据")["default_offset_days"], 2)


class TestTaskIdentity(unittest.TestCase):
    def test_task_key_differs_by_channel_scope(self):
        channel_a = C.make_task("牵牛花", "门店经营指标", "2026-09-30",
                                scope={"scope_id": "channel:100", "channel": "100"})
        channel_b = C.make_task("牵牛花", "门店经营指标", "2026-09-30",
                                scope={"scope_id": "channel:200", "channel": "200"})
        self.assertNotEqual(C.task_key(channel_a), C.task_key(channel_b))

    def test_task_key_is_stable_and_excludes_collection_time(self):
        a = C.make_task("闪购", "评价数据", "2026-09-29")
        b = C.make_task("闪购", "评价数据", "2026-09-29")
        self.assertEqual(C.task_key(a), C.task_key(b))
        b["collected_at"] = "2099-01-01"
        self.assertEqual(C.task_key(a), C.task_key(b))

    def test_snapshot_is_one_task_not_30(self):
        r = reg()
        plan = plan_daily(T, r, {})
        snap = [t for t in plan["tasks"] if t["table"] == "商品流量_近30天"]
        self.assertEqual(len(snap), 1)
        self.assertEqual((snap[0]["window_start"], snap[0]["window_end"]),
                         ("2026-09-01", "2026-09-30"))
        self.assertEqual(snap[0]["business_date"], "2026-09-30")


class TestPlanning(unittest.TestCase):
    def test_invalid_calendar_date_rejected_before_anything(self):
        with self.assertRaises(ValueError):
            plan_daily("2026-02-30", reg(), {}, scope={})

    def test_empty_ledger_gives_target_tasks_plus_gaps(self):
        r = reg()
        plan = plan_daily(T, r, {}, scope={})
        C.validate_plan(plan)
        keys = {t["task_key"] for t in plan["tasks"]}
        # 19 张表的目标日任务全部在计划里（含 T-2 的评价表与快照表的窗口任务）
        self.assertTrue(target_keys(T, r) <= keys)
        self.assertEqual(len(target_keys(T, r)), 19)
        # 快照是一个任务，不是 30 个
        self.assertEqual(len({t["task_key"] for t in plan["tasks"]
                              if t["table"] == "商品流量_近30天"}), 1)

    def test_no_gap_when_ledger_complete_except_target_gap(self):
        r = reg()
        led = ledger_complete_all_but(T, r, keep_missing=target_keys(T, r))
        plan = plan_daily(T, r, led)
        self.assertEqual(len(plan["gap_tasks"]), 19)   # 目标日尚未采 → 19 个缺口
        self.assertEqual(len(plan["tasks"]), 19)       # 补漏与目标日重叠 → 不重复

    def test_gap_detects_middle_missing_day(self):
        r = reg()
        empty = plan_daily(T, r, {})
        hole = [t["task_key"] for t in empty["tasks"]
                if t["table"] == "门店经营指标" and t["business_date"] == "2026-09-27"]
        self.assertEqual(len(hole), 1)
        led = ledger_complete_all_but(T, r, keep_missing=set(hole))
        summary = check_gap(T, r, led)
        got = {g["table"] + "@" + g["business_date"] for g in summary["gaps"]}
        self.assertIn("门店经营指标@2026-09-27", got)
        # 目标日（9-30）本就没采，也应在缺口里，但不能出现「假缺口」
        self.assertNotIn("门店经营指标@2026-09-26", got)

    def test_snapshot_checked_as_target_window_not_history(self):
        r = reg()
        empty = plan_daily(T, r, {})
        snap_key = [t["task_key"] for t in empty["tasks"]
                    if t["table"] == "商品流量_近30天"][0]
        led = ledger_complete_all_but(T, r, keep_missing={snap_key})
        summary = check_gap(T, r, led)
        snap_gaps = [g for g in summary["gaps"] if g["table"] == "商品流量_近30天"]
        self.assertEqual(len(snap_gaps), 1)
        self.assertEqual(snap_gaps[0]["window_start"], "2026-09-01")
        self.assertEqual(snap_gaps[0]["window_end"], "2026-09-30")

    def test_force_recollects_target_even_when_ledger_complete(self):
        r = reg()
        led = ledger_complete_all_but(T, r)
        self.assertEqual(len(plan_daily(T, r, led)["tasks"]), 0)
        forced = plan_daily(T, r, led, force=True)
        self.assertEqual(len(forced["tasks"]), 19)

    def test_resume_keeps_original_business_today_and_batch(self):
        r = reg()
        original = plan_daily(T, r, {}, scope={})
        led = ledger_complete_all_but(T, r)
        resumed = plan_daily(T, r, led, scope={})       # 跨午夜后仍传原来的 T
        self.assertEqual(resumed["business_today"], original["business_today"])
        # 「恢复沿用旧批次」：同一天同一模式 → 同一批次身份
        self.assertEqual(resumed["batch_id"], original["batch_id"])

    def test_independent_rounds_get_distinct_batch_ids(self):
        r = reg()
        a = plan_daily(T, r, {}, force=True, batch_seq="round1")
        b = plan_daily(T, r, {}, force=True, batch_seq="round2")
        self.assertNotEqual(a["batch_id"], b["batch_id"])

    def test_targeted_rejects_future_date(self):
        with self.assertRaises(ValueError):
            plan_targeted(T, ["2026-10-02"], reg(), scope={})

    def test_targeted_marks_snapshot_backfill_unsupported(self):
        plan = plan_targeted(T, ["2026-09-20"], reg(), scope={})
        self.assertEqual(len(plan["unsupported_snapshots"]), 1)
        self.assertTrue(all(t["table"] != "商品流量_近30天" for t in plan["tasks"]))

    def test_targeted_covers_both_platforms(self):
        plan = plan_targeted(T, ["2026-09-20"], reg(), scope={})
        self.assertEqual(len(plan["tasks"]), 18)        # 19 - 快照
        self.assertEqual({t["platform"] for t in plan["tasks"]}, {"牵牛花", "闪购"})


if __name__ == "__main__":
    unittest.main()
