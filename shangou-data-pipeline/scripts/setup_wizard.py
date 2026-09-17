#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""闪购数据管线 · 交互式安装向导。

一次运行完成环境检测与配置：
  1. 环境检测（OS / Python / 依赖 / CDP / 飞书）
  2. 数据目录配置（默认技能目录下 data/）
  3. 历史备份检查（是否有备份需迁移；有→强制本地+飞书）
  4. 产出物保存方式（仅本地 / 本地+飞书）
  5. 飞书对接（lark-cli 或 自建应用API）+ 当场验证
  6. 浏览器与登录（CDP检测 + 人工/自动登录引导）
  7. 历史数据初始化（备份迁移并验证 / Excel迁移 / 回溯采集引导）

用法：
  python3 setup_wizard.py          # 交互式安装
  python3 setup_wizard.py --check   # 仅环境检测
  python3 setup_wizard.py --show    # 查看当前配置
  python3 setup_wizard.py --feishu  # 仅重新配置飞书
"""

import warnings
warnings.filterwarnings("ignore")

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_check import run_full_check, print_human  # noqa: E402

# 配置路径
CONFIG_DIR = Path.home() / ".config" / "shangou-pipeline"
CONFIG_FILE = CONFIG_DIR / "config.json"
FEISHU_KEYS = CONFIG_DIR / "feishu.json"
SKILL_DIR = Path(__file__).parent.parent  # scripts/ 的上一级
REPORT_URL = "https://shangoue.meituan.com"


class C:
    def __init__(self):
        self.enabled = platform.system() != "Windows" or bool(os.environ.get("TERM"))
    def _c(self, code, s):
        return f"\033[{code}m{s}\033[0m" if self.enabled else s
    def blue(self, s): return self._c("34", s)
    def green(self, s): return self._c("32", s)
    def yellow(self, s): return self._c("33", s)
    def red(self, s): return self._c("31", s)
    def bold(self, s): return self._c("1", s)

c = C()
def info(s): print(c.blue("[安装] ") + s)
def ok(s): print(c.green("[完成] ") + s)
def warn(s): print(c.yellow("[注意] ") + s)
def err(s): print(c.red("[错误] ") + s, file=sys.stderr)

def ask(prompt, default=None, choices=None):
    suffix = f" [{'/'.join(choices)}]" if choices else ""
    if default:
        suffix += f" (默认: {default})"
    while True:
        ans = input(f"{prompt}{suffix}: ").strip()
        if not ans and default is not None:
            return default
        if choices and ans and ans.lower() not in [x.lower() for x in choices]:
            warn(f"请输入: {'/'.join(choices)}"); continue
        return ans

def ask_yes_no(prompt, default=False):
    d = "Y/n" if default else "y/N"
    return ask(f"{prompt} [{d}]", default="y" if default else "n").lower().startswith("y")

def ask_password(prompt):
    """安全输入密码（不回显）。"""
    import getpass
    return getpass.getpass(f"{prompt}: ")

def expand_path(p):
    if not p: return p
    p = p.strip().strip('"').strip("'")
    return str(Path(os.path.expanduser(p)).resolve())

def ensure_dir(p):
    try:
        Path(p).mkdir(parents=True, exist_ok=True)
        test = Path(p) / f".write_test_{os.getpid()}"
        test.write_text("ok"); test.unlink()
        return True
    except Exception:
        return False

def save_json(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def load_json(path):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


# ── 步骤1：环境检测 ──────────────────────────────────────

def step_environment(env):
    print(); info(c.bold("步骤 1/6 · 环境检测")); print("-" * 50)
    print_human(env); print()

    # 自动安装缺失依赖（从requirements.txt一次性安装）
    requirements_file = SKILL_DIR / "requirements.txt"
    missing = []
    for pkg in ["playwright", "openpyxl", "pandas", "requests"]:
        if not env["packages"].get(pkg):
            missing.append(pkg)

    if missing:
        warn(f"缺少Python依赖: {', '.join(missing)}")
        if ask_yes_no("是否现在自动安装所有缺失依赖？", True):
            if requirements_file.exists():
                info(f"从 {requirements_file} 安装...")
                r = subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(requirements_file)],
                                   timeout=600)
            else:
                r = subprocess.run([sys.executable, "-m", "pip", "install"] + missing,
                                   timeout=600)
            if r.returncode == 0:
                ok("依赖安装完成")
                # 重新检测
                from env_check import detect_python_packages
                env["packages"] = detect_python_packages()
            else:
                err("依赖安装失败，请手动: pip install -r requirements.txt")
    else:
        ok("所有Python依赖已安装")

    # playwright 浏览器检查
    if env["packages"].get("playwright"):
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                try:
                    pw.chromium.launch(headless=True)
                    pw.chromium._impl_obj.stop()  # cleanup
                except Exception:
                    warn("playwright浏览器未下载，正在安装...")
                    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], timeout=300)
        except Exception:
            pass

    return env


# ── 步骤2：数据目录配置 ──────────────────────────────────

def step_directories(env, existing=None):
    print(); info(c.bold("步骤 2/6 · 数据目录配置")); print("-" * 50)

    # 默认：技能目录下 data/
    default_data_dir = str(SKILL_DIR / "data")

    if existing:
        default_data_dir = existing.get("data_dir", default_data_dir)

    print()
    print("  数据目录说明：")
    print("    raw/     → 临时原始CSV（整合后自动清理）")
    print("    master/  → 累积总表CSV（数据源，供分析技能读取）")
    print("    *.xlsx   → Excel总表（同名覆盖，只保留一份）")
    print()

    data_dir = expand_path(ask("数据根目录", default=default_data_dir))
    raw_dir = str(Path(data_dir) / "raw")
    master_dir = str(Path(data_dir) / "master")

    for d in [data_dir, raw_dir, master_dir, str(Path(master_dir) / "by_table")]:
        if not ensure_dir(d):
            err(f"目录不可写: {d}"); return None
        ok(f"目录就绪: {d}")

    return {
        "data_dir": data_dir,
        "raw_dir": raw_dir,
        "master_dir": master_dir,
        "excel_path": str(Path(data_dir) / "F.LUX品牌数据汇总表.xlsx"),
    }


# ── 步骤3：历史备份检查（必须在保存方式之前） ────────────

def step_backup_check(existing=None):
    print(); info(c.bold("步骤 3/7 · 历史备份数据检查")); print("-" * 50)
    print()
    print("  是否已有历史备份数据（Excel总表/CSV）需要迁移？")
    print()
    print("  选择「有」后：")
    print("    · 将强制开启本地+飞书模式（备份需上传到飞书指定路径）")
    print("    · 备份上传路径 = 飞书同步目标文件夹（与日常同步同一路径）")
    print("    · 安装完成后引导你上传备份，自动验证格式并迁移")
    print("    · 迁移后自动检测数据缺失，缺失时引导执行补采")
    print()
    print("  选择「无」：")
    print("    · 跳过备份迁移，后续可选回溯采集或从今日起累积")
    print()
    has_backup = ask_yes_no("是否有历史备份数据需要迁移？", False)
    return {"has_backup": has_backup}


# ── 步骤4：产出物保存方式 ────────────────────────────────

def step_output_mode(existing=None, has_backup=False):
    print(); info(c.bold("步骤 4/7 · 产出物保存方式")); print("-" * 50)
    print()
    print("  1. 仅本地    — Excel总表保存在本地数据目录")
    print("  2. 本地+飞书 — 本地保存，同时自动上传到飞书云空间（同名覆盖）")
    print()
    if has_backup:
        ok("检测到有历史备份需要迁移 → 强制启用本地+飞书（备份与同步同一路径）")
        return {"output_mode": "local+feishu", "export_excel": True}
    default_mode = "2" if (existing and existing.get("feishu", {}).get("enabled")) else "1"
    mode = ask("选择保存方式", default=default_mode, choices=["1", "2"])
    return {"output_mode": "local+feishu" if mode == "2" else "local", "export_excel": True}


# ── 步骤4：飞书对接 ──────────────────────────────────────

def step_feishu(env, existing=None):
    print(); info(c.bold("步骤 4/6 · 飞书云空间对接")); print("-" * 50)
    fs_env = env["feishu"]
    feishu_cfg = existing.get("feishu", {}) if existing else {}
    method = None

    if fs_env["lark_cli"]["installed"]:
        ok(f"检测到 lark-cli: {fs_env['lark_cli']['path']}")
        if fs_env["lark_cli"]["logged_in"]:
            ok("lark-cli 已登录"); method = "lark-cli"
        elif ask_yes_no("lark-cli 未登录，是否现在运行 lark-cli auth init 扫码？", True):
            try:
                subprocess.run(["lark-cli", "auth", "init"], timeout=180)
                method = "lark-cli"
            except Exception as e:
                err(f"登录失败: {e}")
    else:
        print()
        print("未检测到 lark-cli。两种对接方式：")
        print("  方式A（豆包环境推荐）：lark-cli 扫码授权")
        print("  方式B（通用）：飞书自建应用 App ID + App Secret")
        print()
        print("  方式B步骤：")
        print("    1. https://open.feishu.cn/app 创建企业自建应用")
        print("    2. 权限管理开通 drive:drive（云空间读写）")
        print("    3. 发布应用，确保自己在可用范围内")
        print("    4. 复制 App ID 和 App Secret")
        print()
        app_id = os.environ.get("FEISHU_APP_ID", "") or ask("App ID（留空跳过）", default="")
        app_secret = ""
        if app_id:
            app_secret = os.environ.get("FEISHU_APP_SECRET", "") or ask_password("App Secret")
        if app_id and app_secret:
            method = "api"
            save_json(FEISHU_KEYS, {"app_id": app_id, "app_secret": app_secret})
            ok("飞书自建应用凭据已保存")

    if not method:
        warn("未配置飞书，后续可重新运行安装向导配置")
        return {"enabled": False, "method": None, "folder_token": "", "folder_name": ""}

    folder_token = feishu_cfg.get("folder_token", "")
    folder_name = feishu_cfg.get("folder_name", "")
    if folder_token:
        info(f"当前目标文件夹: {folder_name or folder_token}")
        if not ask_yes_no("是否更换目标文件夹？", False):
            return _feishu_result(method, folder_token, folder_name)

    print()
    print("目标文件夹：在飞书云空间打开目标文件夹，URL中 /folder/ 后字符串即 folder_token")
    print("  示例: https://xxx.feishu.cn/drive/folder/HSeMfORtJlhkt9dv86bcc3MCnue")
    if method == "api":
        print("  注意：自建应用需在文件夹「分享」中添加为协作者(可编辑)")
    print()
    folder_token = ask("文件夹 folder_token", default=folder_token)
    folder_name = ask("文件夹备注名", default=folder_name or "品牌数据")
    return _feishu_result(method, folder_token, folder_name)


def _feishu_result(method, folder_token, folder_name):
    sync_raw = ask_yes_no("是否同时备份每日原始CSV到飞书？", False)
    # 当场验证连通性
    info("验证飞书连通性...")
    setup_script = Path(__file__).parent / "feishu_setup.py"
    r = subprocess.run([sys.executable, str(setup_script), "verify",
                        "--folder-token", folder_token], capture_output=True, text=True)
    try:
        v = json.loads(r.stdout)
        if v.get("success"):
            ok(f"飞书连通性验证通过（{v['result'].get('method')}，文件夹可访问）")
        else:
            warn(f"连通性验证未通过: {v.get('result')}")
            warn("配置已保存，请检查folder_token/应用权限后重新运行 --feishu")
    except Exception:
        warn("连通性验证结果无法解析，请稍后手动验证")
    return {"enabled": True, "method": method, "folder_token": folder_token,
            "folder_name": folder_name, "sync_raw": sync_raw, "sync_excel": True}


# ── 步骤5：浏览器与登录 ──────────────────────────────────

def step_browser(env, existing=None):
    print(); info(c.bold("步骤 5/6 · 浏览器与登录配置")); print("-" * 50)
    cdp = env["cdp"]
    is_windows = env["os"]["type"] == "Windows"

    # Windows下9222常被WorkBuddy内置Edge占用(mock页)，默认用9333
    default_port = 9333 if is_windows else 9222
    port = default_port

    # 检测已有的CDP端口
    available_ports = [p for p, e in cdp["ports"].items() if e["available"]]
    if available_ports:
        for p in available_ports:
            browser = cdp["ports"][p].get("browser", "")
            warn(f"检测到CDP端口 {p}: {browser}")
            # Windows下9222很可能是WorkBuddy内置Edge(mock页)
            if is_windows and p == "9222" and "Edge" in browser:
                warn("  ⚠ 这是WorkBuddy内置Edge，打开的是mock演练页，不是真闪购！")
                warn("  不能用这个端口。请自己启动Chrome连接9333端口。")
        # 如果有非9222的端口可用，优先用
        real_ports = [p for p in available_ports if not (is_windows and p == "9222" and "Edge" in cdp["ports"][p].get("browser", ""))]
        if real_ports:
            port = int(real_ports[0])
            ok(f"使用CDP端口: {port}")
        elif is_windows:
            # Windows上只有WorkBuddy的Edge，需要用户自己开Chrome
            port = 9333
            print()
            warn("Windows环境下需要你自己启动Chrome（带调试端口），不要用WorkBuddy内置浏览器。")
            print()
            print("  请在Windows的CMD/PowerShell中运行：")
            print()
            print('  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" '
                  '--remote-debugging-port=9333 --user-data-dir="C:\\shangou-chrome-profile"')
            print()
            print("  然后在弹出的Chrome中打开 https://shangoue.meituan.com 并登录。")
            print()
            ask("按回车确认Chrome已启动并已登录闪购商家端", default="")
    else:
        print()
        if is_windows:
            print("  Windows环境，请在CMD/PowerShell中运行：")
            print()
            print('  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" '
                  '--remote-debugging-port=9333 --user-data-dir="C:\\shangou-chrome-profile"')
            print()
            print("  然后在Chrome中登录 https://shangoue.meituan.com")
            print()
            port = 9333
        else:
            bs_script = Path(__file__).parent / "browser_setup.py"
            r = subprocess.run([sys.executable, str(bs_script), "launch-guide"], capture_output=True, text=True)
            try:
                guide = json.loads(r.stdout)
                print(f"\n请先关闭所有Chrome窗口，然后运行：\n  {guide['command']}\n")
            except Exception:
                pass
            port = int(ask("CDP端口", default=str(default_port)) or str(default_port))

    print()
    print(f"  登录闪购商家端: {REPORT_URL}")
    print()
    print("  登录方式：")
    print("    1. 人工浏览器登录（推荐）— 在Chrome中手动登录，含滑块/验证码")
    print("    2. 自动短信登录 — 输入手机号，脚本自动填手机号+发送验证码，你只需提供收到的验证码")
    print()
    login_method = ask("选择登录方式", default="1", choices=["1", "2"])

    if login_method == "2":
        phone = ask("手机号", default="")
        if phone:
            info("正在启动自动登录流程...")
            al_script = Path(__file__).parent / "browser_setup.py"
            r = subprocess.run([sys.executable, str(al_script), "auto-login",
                                "--port", str(port), "--phone", phone],
                             capture_output=False, text=True)
            if r.returncode == 0:
                ok("自动登录成功")
            else:
                warn("自动登录未完成（可能需要滑块/人工验证）")
                if not ask_yes_no("是否已在浏览器中手动完成登录？", True):
                    warn("请完成登录后再继续")
    else:
        print(f"  请在Chrome中打开并登录闪购商家端")
        print(f"  登录、滑块、人机验证必须人工完成")
        print()

    # 验证登录态
    if ask_yes_no("已登录闪购商家端？选择是自动验证登录态", True):
        try:
            vs_script = Path(__file__).parent / "browser_setup.py"
            r = subprocess.run([sys.executable, str(vs_script), "verify-login",
                                "--port", str(port)], capture_output=True, text=True, timeout=10)
            v = json.loads(r.stdout)
            if v.get("logged_in"):
                ok("登录态验证通过")
            else:
                warn(f"登录态未确认: {v.get('message', '')}")
                warn("请确保已在Chrome中登录闪购商家端")
        except Exception as e:
            warn(f"自动验证失败({e})，请自行确认登录态")

    return {"cdp_port": port, "cdp_host": "127.0.0.1"}


# ── 步骤7：历史数据初始化 ────────────────────────────────

def step_history(env, dirs, has_backup=False, feishu_cfg=None):
    print(); info(c.bold("步骤 7/7 · 历史数据初始化")); print("-" * 50)
    bt = Path(dirs["master_dir"]) / "by_table"
    existing_csvs = list(bt.glob("*.csv"))

    if existing_csvs:
        ok(f"总表目录已有 {len(existing_csvs)} 张表")
        return {"initialized": True, "table_count": len(existing_csvs)}

    # 有备份：引导上传备份到飞书 → 下载 → 验证 → 迁移 → 缺失检测
    if has_backup:
        return _migrate_from_backup(env, dirs, feishu_cfg)

    print()
    print("  新环境总表为空。数据分析至少需要近30日历史数据：")
    print("    方式A：从已有Excel总表迁移（推荐）")
    print("    方式B：自动回溯采集近30日数据")
    print("    方式C：跳过（仅从今日开始累积）")
    print()
    choice = ask("选择初始化方式", default="A", choices=["A", "B", "C"])

    if choice.upper() == "A":
        excel_path = expand_path(ask("Excel总表文件路径"))
        if Path(excel_path).exists():
            converter = Path(__file__).parent / "excel_to_csv.py"
            info("正在从Excel迁移...")
            r = subprocess.run([sys.executable, str(converter), "--excel", excel_path,
                                "--master", dirs["master_dir"]])
            if r.returncode == 0:
                ok("Excel总表迁移完成")
                return {"initialized": True, "source": "excel", "path": excel_path}
            err("迁移失败，请检查Excel格式")
        else:
            err(f"文件不存在: {excel_path}")

    elif choice.upper() == "B":
        print()
        info("回溯采集需要浏览器已登录闪购商家端")
        print("  脚本将自动逐日采集近30日数据并整合")
        if ask_yes_no("是否现在开始回溯采集？", True):
            days = int(ask("采集天数", default="30"))
            print()
            info(f"即将回溯采集近{days}天数据")
            print("  采集命令：")
            print(f"    python3 scripts/shangou_report_download.py all --daily")
            print(f"    然后运行 run_pipeline.sh 整合")
            print()
            warn("回溯采集需要浏览器保持登录，可能耗时较长")
            return {"initialized": False, "source": "backfill", "guide": "backfill", "days": days}
        return {"initialized": False, "source": "backfill_skipped"}

    else:
        warn("跳过历史数据初始化，总表将从今日开始累积")
        return {"initialized": False, "source": "skip"}

    return {"initialized": False}


def _find_lark_backup(folder_token, feishu_cfg=None):
    """在飞书文件夹中查找备份Excel/CSV总表文件。"""
    method = (feishu_cfg or {}).get("method") or "lark-cli"
    # 用lark-cli列出文件夹
    try:
        cmd = ["lark-cli", "drive", "files", "list",
               "--params", json.dumps({"folder_token": folder_token, "page_size": 200}),
               "--format", "json"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        data = json.loads(r.stdout)
        d = data.get("data", data)
        files = d.get("files", [])
    except Exception as e:
        return [], str(e)
    return files, None


def _migrate_from_backup(env, dirs, feishu_cfg=None):
    """有备份时的迁移：飞书下载备份 → 验证格式 → 迁移 → 缺失检测。"""
    folder_token = (feishu_cfg or {}).get("folder_token", "")
    folder_name = (feishu_cfg or {}).get("folder_name", "品牌数据")
    if not folder_token:
        err("未配置飞书同步文件夹，无法从飞书拉取备份")
        return {"initialized": False, "source": "error", "message": "no_feishu_folder"}

    print()
    warn(f"请在飞书云空间「{folder_name}」文件夹（folder_token: {folder_token}）中上传历史备份文件")
    print("  支持：F.LUX品牌数据汇总表.xlsx 或 任意含标准表名的Excel/CSV总表")
    print("  标准表名：门店财务明细 / 门店成交明细 / 商品数据 / 问题订单数据 /")
    print("           流量明细(新) / 流量渠道明细(新) / 评价数据 / 售后订单数据")
    print()
    if not ask_yes_no("已上传备份到飞书？选择是开始自动检测并迁移", True):
        warn("跳过备份迁移，总表将从今日开始累积")
        return {"initialized": False, "source": "backup_deferred"}

    # 列出飞书文件夹，找候选备份文件
    info(f"正在扫描飞书文件夹 {folder_name} ...")
    files, lerr = _find_lark_backup(folder_token, feishu_cfg)
    if lerr:
        err(f"无法列出飞书文件夹: {lerr}")
        return {"initialized": False, "source": "error", "message": lerr}

    excel_candidates = []
    csv_candidates = []
    for f in files:
        name = f.get("name", "")
        if name.endswith((".xlsx", ".xls")):
            excel_candidates.append(f)
        elif name.endswith(".csv"):
            csv_candidates.append(f)
    candidates = excel_candidates + csv_candidates
    if not candidates:
        err(f"飞书文件夹中未找到备份文件（共{len(files)}个文件）")
        return {"initialized": False, "source": "error", "message": "no_backup_found"}

    # 让用户选择候选文件（默认第一个）
    print()
    info("找到以下候选备份文件：")
    for i, f in enumerate(candidates):
        tag = "Excel" if f.get("name", "").endswith((".xlsx", ".xls")) else "CSV"
        print(f"  [{i+1}] {f.get('name')}  ({tag})")
    sel = ask("选择要迁移的备份文件", default="1")
    try:
        idx = int(sel) - 1
        chosen = candidates[idx]
    except Exception:
        err("选择无效")
        return {"initialized": False, "source": "error", "message": "invalid_selection"}

    # 下载备份到临时目录
    dl_dir = Path(dirs["data_dir"]) / "backup_download"
    dl_dir.mkdir(parents=True, exist_ok=True)
    local_path = dl_dir / chosen.get("name", "backup.xlsx")
    info(f"下载备份: {chosen.get('name')}")
    try:
        r = subprocess.run(["lark-cli", "drive", "+download",
                            "--file-token", chosen.get("token"),
                            "--output", str(local_path)],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0 or not local_path.exists():
            err(f"下载失败: {r.stderr[:200]}")
            return {"initialized": False, "source": "error", "message": "download_failed"}
    except Exception as e:
        err(f"下载异常: {e}")
        return {"initialized": False, "source": "error", "message": str(e)}
    ok(f"下载完成: {local_path} ({local_path.stat().st_size/1024:.0f}KB)")

    # 格式验证：Excel检查sheet名 / CSV检查表名
    info("验证备份格式...")
    if local_path.suffix.lower() in (".xlsx", ".xls"):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(local_path, read_only=True)
            sheets = wb.sheetnames
            wb.close()
            std = {"门店财务明细", "门店成交明细", "商品数据", "问题订单数据",
                   "流量明细(新)", "流量渠道明细(新)", "评价数据", "售后订单数据"}
            matched = [s for s in sheets if s in std]
            if not matched:
                err(f"Excel中未找到标准表名。实际sheet: {sheets}")
                err("请检查备份文件是否符合标准8表结构，或改用 setup.sh --excel <文件> 迁移")
                return {"initialized": False, "source": "error", "message": "format_mismatch"}
            ok(f"格式验证通过：匹配 {len(matched)} 张标准表 {matched}")
        except Exception as e:
            err(f"Excel读取失败: {e}")
            return {"initialized": False, "source": "error", "message": str(e)}
    else:
        # CSV：文件名需含标准表名
        std_names = ["门店财务明细", "门店成交明细", "商品数据", "问题订单数据",
                     "流量明细(新)", "流量渠道明细(新)", "评价数据", "售后订单数据"]
        matched = [n for n in std_names if n in chosen.get("name", "")]
        if not matched:
            err(f"CSV文件名未匹配标准表名: {chosen.get('name')}")
            return {"initialized": False, "source": "error", "message": "format_mismatch"}
        ok(f"格式验证通过：匹配 {len(matched)} 张标准表 {matched}")

    # 迁移：Excel走excel_to_csv.py；单CSV直接复制
    converter = Path(__file__).parent / "excel_to_csv.py"
    if local_path.suffix.lower() in (".xlsx", ".xls"):
        info("正在从备份Excel迁移...")
        r = subprocess.run([sys.executable, str(converter), "--excel", str(local_path),
                            "--master", dirs["master_dir"]], capture_output=True, text=True)
        if r.returncode != 0:
            err(f"迁移失败: {r.stderr[-300:]}")
            return {"initialized": False, "source": "error", "message": "migrate_failed"}
    else:
        # 单个CSV：按文件名匹配表名复制到master/by_table
        bt = Path(dirs["master_dir"]) / "by_table"
        bt.mkdir(parents=True, exist_ok=True)
        import shutil
        for std_name in std_names:
            if std_name in chosen.get("name", ""):
                dest = bt / f"{std_name}.csv"
                shutil.copy(local_path, dest)
                ok(f"CSV已迁移: {std_name}.csv")
    ok("备份迁移完成")

    # 缺失检测：检查各总表CSV行数与日期范围
    info("检测数据缺失...")
    bt = Path(dirs["master_dir"]) / "by_table"
    import pandas as pd
    missing_report = []
    for csvf in sorted(bt.glob("*.csv")):
        if csvf.name == "_update_log.csv":
            continue
        try:
            df = pd.read_csv(csvf)
            n = len(df)
            # 找日期列
            date_col = None
            for col in ["日期", "开始时间", "开始日期", "下单时间", "评价提交日期"]:
                if col in df.columns:
                    date_col = col
                    break
            if date_col:
                dmin, dmax = str(df[date_col].min())[:10], str(df[date_col].max())[:10]
                status = f"行数{n} 日期[{dmin} ~ {dmax}]"
            else:
                status = f"行数{n} 无日期列"
            print(f"  ✓ {csvf.stem}: {status}")
        except Exception as e:
            missing_report.append(f"{csvf.stem}: 读取失败 {e}")
            print(f"  ✗ {csvf.stem}: 读取失败 {e}")
    if missing_report:
        warn("以下总表异常，可能需要补采：")
        for m in missing_report:
            warn(f"  - {m}")
        print()
        warn("如需补齐历史数据，可运行补采脚本：")
        print("    python3 scripts/shangou_report_download.py all --daily")
        print("    然后运行 run_pipeline.sh 整合")
        if ask_yes_no("是否现在执行补采？（需浏览器保持登录）", False):
            return {"initialized": True, "source": "backup", "need_backfill": True}
    else:
        ok("未发现缺失，总表数据完整")

    return {"initialized": True, "source": "backup", "path": str(local_path)}


# ── 主流程 ────────────────────────────────────────────────

def print_summary(cfg, env):
    print()
    print(c.bold("=" * 62))
    print(c.green("  闪购数据管线安装完成"))
    print(c.bold("=" * 62))
    print(f"\n  Agent环境: {env['agent']['type']}")
    print(f"  操作系统: {env['os']['type_display']}")
    print(f"  数据目录: {cfg['data_dir']}")
    print(f"  CDP端口: {cfg.get('cdp_port', 9222)}")
    print(f"  产出物方式: {cfg.get('output_mode', 'local')}")
    print(f"  历史备份: {'有（已迁移）' if cfg.get('has_backup') else '无'}")
    f = cfg.get("feishu", {})
    print(f"  飞书对接: {f.get('method') + ' → ' + f.get('folder_name','') if f.get('enabled') else '未启用'}")
    print()
    print("  日常使用：")
    print("    bash scripts/run_pipeline.sh   # 一键采集+整合+导出")
    print("    python3 scripts/setup_wizard.py --show   # 查看当前配置")
    print(c.bold("=" * 62))


def main():
    ap = argparse.ArgumentParser(description="闪购数据管线交互式安装向导")
    ap.add_argument("--check", action="store_true", help="仅环境检测")
    ap.add_argument("--show", action="store_true", help="查看当前配置")
    ap.add_argument("--feishu", action="store_true", help="仅重新配置飞书")
    args = ap.parse_args()

    if args.show:
        if CONFIG_FILE.exists():
            print(CONFIG_FILE.read_text(encoding="utf-8"))
        else:
            print("尚未配置，运行 python3 setup_wizard.py 开始安装")
        return

    env = run_full_check()

    if args.check:
        print_human(env)
        return

    existing = load_json(CONFIG_FILE)

    if args.feishu:
        feishu_cfg = step_feishu(env, existing)
        existing["feishu"] = feishu_cfg
        save_json(CONFIG_FILE, existing)
        ok("飞书配置已更新")
        return

    print(c.bold("=" * 62))
    print(c.bold("  闪购数据管线 · 交互式安装向导"))
    print(c.bold("=" * 62))
    print()
    print("  本引导一次性完成：环境检测 → 目录配置 → 备份检查 → 保存方式 → 飞书对接 → 浏览器登录 → 历史数据")
    print()

    # 7步流程
    env = step_environment(env)
    dirs = step_directories(env, existing)
    if not dirs:
        err("目录配置失败，安装中止"); sys.exit(1)
    backup_cfg = step_backup_check(existing)      # 步骤3：是否有历史备份（在保存方式之前）
    has_backup = backup_cfg.get("has_backup", False)
    output = step_output_mode(existing, has_backup)   # 步骤4：保存方式（有备份→强制本地+飞书）
    if output["output_mode"] == "local+feishu":
        feishu_cfg = step_feishu(env, existing)   # 步骤5：飞书对接（有备份时必须有）
        if has_backup and not feishu_cfg.get("enabled"):
            err("选择了有历史备份，必须启用飞书同步（备份需上传到飞书路径），请重新配置飞书")
            sys.exit(1)
    else:
        feishu_cfg = {"enabled": False, "method": None}
    browser_cfg = step_browser(env, existing)     # 步骤6：浏览器与登录
    history = step_history(env, dirs, has_backup, feishu_cfg)   # 步骤7：历史数据初始化

    # 保存配置
    cfg = {
        "version": "2.0",
        "installed_at": existing.get("installed_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "environment": {"os": env["os"]["type_display"], "agent": env["agent"]["type"]},
        "data_dir": dirs["data_dir"],
        "raw_dir": dirs["raw_dir"],
        "master_dir": dirs["master_dir"],
        "excel_path": dirs["excel_path"],
        "output_mode": output["output_mode"],
        "export_excel": output.get("export_excel", True),
        "cleanup_raw_after_integrate": True,
        "has_backup": has_backup,
        "feishu": feishu_cfg,
        "cdp_port": browser_cfg["cdp_port"],
        "cdp_host": browser_cfg["cdp_host"],
        "history": history,
        "min_history_days": 30,
    }
    save_json(CONFIG_FILE, cfg)
    ok(f"配置已保存: {CONFIG_FILE}")
    print_summary(cfg, env)


if __name__ == "__main__":
    main()
