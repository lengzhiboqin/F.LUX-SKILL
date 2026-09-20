#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""闪购数据管线 — 一键闭环（跨平台Python入口）。

采集8张表 → 核对 → 追加总表 → 清理原始 → 导出Excel → 飞书同步 → 摘要。
所有路径在Python内部处理，不经过shell，避免Windows/Git Bash路径问题。

退出码：
  0 = 管线成功
  1 = 部分表下载失败 或 追加总表有警告
  2 = 未登录/浏览器不可用/配置缺失
  3 = 环境错误（缺依赖/配置不是向导生成的）
  4 = 美团反爬/人机识别拦截，已清空cookie，需重新登录
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = Path.home() / ".config" / "shangou-pipeline" / "config.json"
COOKIE_VALIDITY_DAYS = 10  # cookie有效期7-15天，取中间值10天提醒


def info(s): print(f"\033[94m[pipeline]\033[0m {s}", flush=True)
def ok(s): print(f"\033[92m[pipeline]\033[0m {s}", flush=True)
def warn(s): print(f"\033[93m[pipeline]\033[0m {s}", flush=True)
def err(s): print(f"\033[91m[pipeline]\033[0m {s}", file=sys.stderr, flush=True)


def run(cmd, **kw):
    """运行子进程，实时输出。"""
    return subprocess.run(cmd, **kw)


def check_cookie_expiry_local():
    """检查cookie是否即将过期（超过COOKIE_VALIDITY_DAYS天）。
    读取配置文件中的last_login_time，如果超过有效期则打印提醒。"""
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, encoding="utf-8") as f:
                cfg = json.load(f)
            last_login = cfg.get("last_login_time", "")
            if last_login:
                last_dt = datetime.strptime(last_login, "%Y-%m-%d %H:%M:%S")
                days_since = (datetime.now() - last_dt).days
                if days_since >= COOKIE_VALIDITY_DAYS:
                    warn(f"⚠️ cookie已使用{days_since}天，接近有效期（{COOKIE_VALIDITY_DAYS}天），建议重新登录")
                    return True
    except Exception:
        pass
    return False


def main():
    # 1. 读取配置
    if not CONFIG_FILE.exists():
        err(f"未找到配置文件: {CONFIG_FILE}")
        err("⛔ 禁止手写 config.json。必须运行交互式安装向导:")
        err(f"   python3 {SCRIPT_DIR / 'setup_wizard.py'}")
        sys.exit(3)

    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        err(f"配置文件JSON解析失败: {e}")
        sys.exit(3)

    # 1b. 硬校验：必须由向导生成
    if cfg.get("version") != "2.0":
        err("配置文件不是由安装向导生成的（缺少 version=2.0 标记）。")
        err("⛔ 这说明配置是手写的或不完整。必须运行交互式安装向导:")
        err(f"   python3 {SCRIPT_DIR / 'setup_wizard.py'}")
        err("")
        err("当前配置内容（供排查）:")
        err(json.dumps(cfg, ensure_ascii=False, indent=2))
        sys.exit(3)

    raw_dir = Path(cfg["raw_dir"])
    master_dir = Path(cfg["master_dir"])
    excel_path = Path(cfg["excel_path"])
    cleanup_raw = cfg.get("cleanup_raw_after_integrate", True)
    export_excel = cfg.get("export_excel", True)
    feishu_enabled = cfg.get("feishu", {}).get("enabled", False)
    timeout = os.environ.get("COLLECT_TIMEOUT", "180")

    raw_dir.mkdir(parents=True, exist_ok=True)
    (master_dir / "by_table").mkdir(parents=True, exist_ok=True)

    info(f"数据目录: {master_dir}")
    info(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 2. 依赖检查
    try:
        import playwright  # noqa: F401
    except ImportError:
        err("缺少 playwright，请运行: pip install playwright")
        sys.exit(3)

    py = sys.executable

    # 3. 登录态探测
    print()
    info("=== 步骤1：探测浏览器登录态 ===")
    # 检查cookie是否即将过期
    check_cookie_expiry_local()
    probe = run([py, str(SCRIPT_DIR / "shangou_report_download.py"), "probe"],
                capture_output=True, text=True, timeout=60)
    print(probe.stdout)
    if '"anti_bot": true' in probe.stdout or probe.returncode == 4:
        err("美团反爬/人机识别拦截，已清空cookie并打开登录页，请人工重新登录后重跑")
        sys.exit(4)
    if probe.returncode != 0 or '"logged_in": false' in probe.stdout:
        err("浏览器不可用或未登录，需人工登录后重跑")
        sys.exit(2)
    ok("登录态正常")

    # 4. 全量daily采集
    print()
    info("=== 步骤2：采集8张报表 ===")
    collect = run([py, str(SCRIPT_DIR / "shangou_report_download.py"), "all", "--daily",
                   "--out", str(raw_dir), "--timeout", timeout])
    collect_rc = collect.returncode
    if collect_rc == 4:
        err("采集过程中触发美团反爬拦截，已清空cookie并打开登录页，请人工重新登录后重跑")
        sys.exit(4)
    if collect_rc != 0:
        warn(f"部分报表下载失败（退出码 {collect_rc}），继续整合已成功的表")

    # 5. 核对采集产出物
    print()
    info("=== 步骤3：核对采集产出物 ===")
    run([py, str(SCRIPT_DIR / "check_files.py"), "--raw", str(raw_dir)])
    latest_date = ""
    try:
        chk = run([py, str(SCRIPT_DIR / "check_files.py"), "--raw", str(raw_dir), "--json"],
                  capture_output=True, text=True, timeout=15)
        if chk.returncode == 0:
            latest_date = json.loads(chk.stdout).get("date", "")
    except Exception:
        pass
    # 兜底：如果check_files没返回日期，使用昨日日期（每日采集任务采集的是昨日数据）
    if not latest_date:
        yesterday = datetime.now() - timedelta(days=1)
        latest_date = yesterday.strftime("%Y-%m-%d")
        info(f"check_files未返回日期，使用默认昨日日期: {latest_date}")

    # 6. 追加到总表
    print()
    info("=== 步骤4：追加数据到总表 ===")
    append = run([py, str(SCRIPT_DIR / "append_master.py"), "--raw", str(raw_dir),
                  "--master", str(master_dir)])
    append_rc = append.returncode
    if append_rc != 0:
        warn(f"追加过程有警告（退出码 {append_rc}）")

    # 7. 缺失检测（内联Python，避免shell here-doc）
    print()
    info("=== 步骤5：数据完整性检测 ===")
    import csv as _csv
    import glob as _glob
    import re as _re
    from datetime import datetime as _dt, timedelta as _td
    bt = master_dir / "by_table"
    all_dates, raw_dates = set(), set()
    for f in bt.glob("*.csv"):
        with open(f, encoding="utf-8-sig") as fh:
            r = list(_csv.reader(fh))
        for row in r[1:]:
            if row and row[-1] and _re.match(r"\d{4}-\d{2}-\d{2}", row[-1]):
                all_dates.add(row[-1][:10])
    for d in raw_dir.iterdir():
        if d.is_dir() and _re.match(r"\d{4}-\d{2}-\d{2}$", d.name):
            raw_dates.add(d.name)
    if not all_dates:
        print("总表无数据")
    else:
        sd = sorted(all_dates)
        print(f"总表采集日范围: {sd[0]} ~ {sd[-1]}（共{len(sd)}天）")
        start = _dt.strptime(sd[0], "%Y-%m-%d")
        end = _dt.strptime(sd[-1], "%Y-%m-%d")
        expected = set()
        cur = start
        while cur <= end:
            expected.add(cur.strftime("%Y-%m-%d"))
            cur += _td(days=1)
        missing = sorted(expected - all_dates)
        print(f"缺失日期: {missing}" if missing else "日期连续，无缺失")
        rnm = sorted(raw_dates - all_dates)
        if rnm:
            print(f"raw有但未入总表: {rnm}")

    # 8. 清理原始数据（清理前确认追加成功）
    if cleanup_raw:
        print()
        info("=== 步骤6：清理临时原始数据 ===")
        # 清理前检查：如果追加失败，不清理原始数据
        if append_rc != 0:
            err(f"追加过程有错误（退出码 {append_rc}），为防止数据丢失，跳过清理原始数据")
            err("请检查追加日志，修复后手动清理: " + str(raw_dir))
        else:
            run([py, str(SCRIPT_DIR / "cleanup_raw.py"), "--raw", str(raw_dir),
                 "--master", str(master_dir), "--keep-days", "0"])

    # 9. 导出Excel
    if export_excel:
        print()
        info("=== 步骤7：导出Excel总表 ===")
        log_msg = f"管线更新 {latest_date} {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        run([py, str(SCRIPT_DIR / "export_excel.py"), "--master", str(master_dir),
             "--output", str(excel_path), "--log", log_msg])

    # 10. 飞书同步
    if feishu_enabled:
        print()
        info("=== 步骤8：同步飞书云空间 ===")
        args = [py, str(SCRIPT_DIR / "feishu_sync.py")]
        if export_excel:
            args += ["--excel", str(excel_path)]
        run(args)

    # 11. 写结构化摘要
    summary_file = master_dir / "last_pipeline.json"
    summary = {
        "ok": collect_rc == 0 and append_rc == 0,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "raw_dir": str(raw_dir), "master_dir": str(master_dir),
        "latest_date": latest_date,
        "excel": excel_path.exists(),
        "collect_rc": collect_rc, "append_rc": append_rc,
        "tables": {},
    }
    for f in sorted(bt.glob("*.csv")):
        name = f.stem
        if name.startswith("_"):
            continue
        with open(f, encoding="utf-8-sig") as fh:
            r = list(_csv.reader(fh))
        dates = sorted(set(row[-1][:10] for row in r[1:] if row and row[-1]))
        summary["tables"][name] = {"rows": len(r) - 1, "cols": len(r[0]) if r else 0,
                                    "collect_dates": dates}
    summary_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"摘要已写入: {summary_file}")

    # 11.5 写统一格式的last_run.json（供validator检测运行状态）
    try:
        last_run_file = SCRIPT_DIR.parent / "data" / "last_run.json"
        last_run_file.parent.mkdir(parents=True, exist_ok=True)
        last_run = {
            "ok": collect_rc == 0 and append_rc == 0,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "data_date": latest_date,
            "exit_code": 0 if (collect_rc == 0 and append_rc == 0) else 1,
            "error": None if (collect_rc == 0 and append_rc == 0) else f"collect_rc={collect_rc}, append_rc={append_rc}",
            "outputs": [str(excel_path)] if export_excel and excel_path.exists() else [],
        }
        last_run_file.write_text(json.dumps(last_run, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"统一状态已写入: {last_run_file}")
    except Exception as e:
        print(f"⚠️ 写last_run.json失败: {e}")

    # 12. 总结
    print()
    info("=== 管线完成 ===")
    ok(f"CSV总表(数据源): {bt}")
    if export_excel:
        ok(f"Excel总表: {excel_path}")
    ok(f"结构化摘要: {summary_file}")

    if collect_rc != 0 or append_rc != 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
