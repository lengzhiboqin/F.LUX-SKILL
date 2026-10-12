# -*- coding: utf-8 -*-
"""F.LUX 日常技能包：**薄命令入口**。

只做参数解析与路由，**不承载第二套业务算法**：
- 规划/采集/验收 → `run_daily`
- 清洗/入库 → `clean` / `db`
- 自检 → `selfcheck`
- 恢复/汇报 → `recover_report`
- 查询 → `query.mjs`（Node，只读）

命令
    check-gap  --today --config
    collect    --today --config [--date --table --force]
    process    --manifest --rules --master [--db]
    selfcheck  --db [--source-root --mode --start --end --table --store --scope
                     --sample-size --seed]
    retain     --db --today --backup-dir [--compact] [--dry-run]
    resume     --manifest --config --rules --master [--db]
    report     --manifest
    run        --today --config --rules --master --db
"""
import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import contracts as C      # noqa: E402
from common import dates               # noqa: E402
from common import registry as REG     # noqa: E402

DEFAULT_CONFIG = {
    "data_root": "./_data",
    "ledger_path": "./_data/_ledger.json",
    "batch_dir": "./_data/_batches",
    "db_path": "./_data/flux.db",
    "backup_dir": "./_data/_backup",
    "rules_path": "./references/cleaning_rules.json",
    "master_path": "./references/store_master.example.json",
    "registry_path": "./references/table_registry.json",
    "hist_path": "./references/historical_headers.json",
    "cdp_host": "127.0.0.1",
    "cdp_port": 9222,
    "timeout_s": 180,
    "attempts": 1,
    "explicit_date": False,
}


def load_config(path=None, overrides=None):
    cfg = dict(DEFAULT_CONFIG)
    if path:
        cfg.update(C.read_json(path))
    cfg.update(overrides or {})
    root = os.path.dirname(os.path.abspath(path)) if path else os.getcwd()
    for k in ("data_root", "ledger_path", "batch_dir", "db_path", "backup_dir",
              "rules_path", "master_path", "registry_path", "hist_path"):
        v = cfg.get(k)
        if v and not os.path.isabs(v):
            cfg[k] = os.path.normpath(os.path.join(root, v))
    cfg["_registry"] = REG.load_registry(cfg.get("registry_path"))
    return cfg


def _save_manifest(cfg, manifest):
    d = os.path.join(cfg["batch_dir"], manifest["batch_id"])
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "manifest.json")
    C.atomic_write_json(p, manifest)
    return p


def _print(obj):
    import json
    print(json.dumps(obj, ensure_ascii=False, indent=1, default=str))


# ---------------- 子命令 ----------------
def cmd_check_gap(args):
    from run_daily import check_gap, load_ledger
    cfg = load_config(args.config)
    t = dates.normalize_iso(args.today or dates.today_shanghai())
    summary = check_gap(t, cfg["_registry"], load_ledger(cfg["ledger_path"]))
    _print(summary)
    return 0


def cmd_collect(args):
    from run_daily import (load_ledger, plan_daily, plan_targeted, run_collection,
                           save_ledger, summarize_manifest)
    cfg = load_config(args.config)
    t = dates.normalize_iso(args.today or dates.today_shanghai())
    cfg["batch_id"] = ""                      # 由规划决定
    ledger = load_ledger(cfg["ledger_path"])
    reg = cfg["_registry"]

    if args.date:
        plan = plan_targeted(t, [args.date], reg)
    else:
        plan = plan_daily(t, reg, ledger, force=bool(args.force),
                          batch_seq=args.batch_seq or "")
    if args.table:
        keep = [x for x in plan["tasks"] if x["table"] == args.table]
        if not keep:
            _print({"error": "该表不在本次计划里：%s" % args.table})
            return 2
        plan["tasks"] = keep
        plan["gap_tasks"] = [x for x in plan["gap_tasks"] if x["table"] == args.table]
    cfg["batch_id"] = plan["batch_id"]
    cfg["explicit_date"] = bool(args.date)

    from run_daily import load_historical
    hist = load_historical(cfg.get("hist_path"))
    manifest = run_collection(plan, cfg, hist=hist, ledger=ledger)
    save_ledger(cfg["ledger_path"], ledger)
    p = _save_manifest(cfg, manifest)
    _print({"manifest": p, **summarize_manifest(manifest)})
    return 0 if C.is_collection_complete(manifest) else 1


def cmd_process(args):
    from clean import load_master, load_rules, process_batch
    manifest = C.read_json(args.manifest)
    rules = load_rules(args.rules)
    master = load_master(args.master)
    if not args.db:
        prepared = process_batch(manifest, rules, master)
        out = os.path.join(os.path.dirname(os.path.abspath(args.manifest)),
                           "prepared.json")
        C.atomic_write_json(out, prepared)
        _print({"prepared": out, "stats": prepared["stats"],
                "note": "未指定 --db，只清洗不写库"})
        return 0
    res = process_batch(manifest, rules, master, db_path=args.db,
                        t=manifest["business_today"])
    _print(res)
    return 0


def cmd_selfcheck(args):
    from selfcheck import check_database
    req = {"mode": args.mode, "tables": [args.table] if args.table else None,
           "start": args.start, "end": args.end,
           "store_ids": [args.store] if args.store else [],
           "scope_ids": [args.scope] if args.scope else [],
           "sample_size": args.sample_size, "seed": args.seed}
    res = check_database(args.db, req, source_root=args.source_root)
    _print({k: res[k] for k in ("passed", "checked_rows", "issues",
                                "source_unavailable", "sample_rule",
                                "expired_rows_reported")})
    return 0 if res["passed"] else 1


def cmd_retain(args):
    import db as DB
    t = dates.normalize_iso(args.today or dates.today_shanghai())
    res = DB.maintain_retention(args.db, t, backup_dir=args.backup_dir,
                                compact=bool(args.compact), dry_run=bool(args.dry_run))
    _print(res)
    return 0


def cmd_resume(args):
    from clean import load_master, load_rules
    from recover_report import render_report, resume_batch
    manifest = C.read_json(args.manifest)
    cfg = load_config(args.config)
    cfg["batch_id"] = manifest["batch_id"]
    rules = load_rules(args.rules) if args.rules else None
    master = load_master(args.master) if args.master else None
    out = resume_batch(manifest, cfg, rules, master, db_path=args.db)
    p = _save_manifest(cfg, out)
    print(render_report(out, write_result=out.get("write_result"),
                        evidence_path=p))
    return 0 if C.is_collection_complete(out) else 1


def cmd_report(args):
    from recover_report import render_report
    manifest = C.read_json(args.manifest)
    print(render_report(manifest, evidence_path=args.manifest))
    return 0


def cmd_run(args):
    """完整链路：缺口 → 采集 → 清洗入库 → 自检 → 汇报。任一步失败即停。"""
    from clean import load_master, load_rules, process_batch
    from recover_report import render_report
    from run_daily import (load_historical, load_ledger, plan_daily, run_collection,
                           save_ledger)
    from selfcheck import check_database

    cfg = load_config(args.config)
    t = dates.normalize_iso(args.today or dates.today_shanghai())
    ledger = load_ledger(cfg["ledger_path"])
    plan = plan_daily(t, cfg["_registry"], ledger)
    cfg["batch_id"] = plan["batch_id"]

    manifest = run_collection(plan, cfg, hist=load_historical(cfg.get("hist_path")),
                              ledger=ledger)
    save_ledger(cfg["ledger_path"], ledger)
    p = _save_manifest(cfg, manifest)

    if not C.is_collection_complete(manifest):
        print(render_report(manifest, evidence_path=p))
        return 1

    rules = load_rules(args.rules)
    master = load_master(args.master)
    wr = process_batch(manifest, rules, master, db_path=args.db, t=t)
    ck = check_database(args.db, {"mode": "sample", "sample_size": 50})
    print(render_report(manifest, write_result=wr, check_result=ck, evidence_path=p))
    return 0 if ck["passed"] else 1


# ---------------- 入口 ----------------
def build_parser():
    ap = argparse.ArgumentParser(prog="flux", description="F.LUX 日常采集 / 清洗入库 / 自检 / 汇报")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check-gap"); p.add_argument("--today"); p.add_argument("--config")
    p.set_defaults(func=cmd_check_gap)

    p = sub.add_parser("collect")
    p.add_argument("--today"); p.add_argument("--config")
    p.add_argument("--date"); p.add_argument("--table")
    p.add_argument("--force", action="store_true")
    p.add_argument("--batch-seq", dest="batch_seq")
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("process")
    p.add_argument("--manifest", required=True); p.add_argument("--rules", required=True)
    p.add_argument("--master", required=True); p.add_argument("--db")
    p.set_defaults(func=cmd_process)

    p = sub.add_parser("selfcheck")
    p.add_argument("--db", required=True); p.add_argument("--source-root")
    p.add_argument("--mode", default="full", choices=["full", "sample"])
    p.add_argument("--start"); p.add_argument("--end"); p.add_argument("--table")
    p.add_argument("--store"); p.add_argument("--scope")
    p.add_argument("--sample-size", dest="sample_size", type=int, default=50)
    p.add_argument("--seed", type=int, default=20261001)
    p.set_defaults(func=cmd_selfcheck)

    p = sub.add_parser("retain")
    p.add_argument("--db", required=True); p.add_argument("--today")
    p.add_argument("--backup-dir", dest="backup_dir", required=True)
    p.add_argument("--compact", action="store_true")
    p.add_argument("--dry-run", dest="dry_run", action="store_true")
    p.set_defaults(func=cmd_retain)

    p = sub.add_parser("resume")
    p.add_argument("--manifest", required=True); p.add_argument("--config")
    p.add_argument("--rules"); p.add_argument("--master"); p.add_argument("--db")
    p.set_defaults(func=cmd_resume)

    p = sub.add_parser("report"); p.add_argument("--manifest", required=True)
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("run")
    p.add_argument("--today"); p.add_argument("--config")
    p.add_argument("--rules", required=True); p.add_argument("--master", required=True)
    p.add_argument("--db", required=True)
    p.set_defaults(func=cmd_run)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ValueError as e:                       # 业务校验类错误：明确报错、非零退出
        print("配置/数据校验失败：%s" % e, file=sys.stderr)
        return 2
    except FileNotFoundError as e:
        print("文件缺失：%s" % e, file=sys.stderr)
        return 2
    except sqlite3.Error as e:                    # 库不存在/打不开/结构异常：不抛裸栈
        print("数据库不可用：%s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
