#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
牵牛花数据采集 Skill - 交互式安装向导
7步引导用户完成环境配置、浏览器登录、品牌确认、路径验证、历史初始化
"""

import sys
import json
import logging
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (
    CONFIG_DIR, CONFIG_FILE, load_config, save_config, is_configured,
    get_skill_dir, get_data_dir, ensure_dir, print_banner, print_step,
    confirm, ask_input, setup_logging, FLUX_KEYWORDS, EXCLUDE_KEYWORDS,
    filter_flux_stores
)

# ============================================================
# 安装向导状态
# ============================================================

class SetupWizard:
    def __init__(self):
        self.config = {
            "version": "1.0",
            "setup_date": "",
            "data_dir": "",
            "chrome_port": 9222,
            "base_url": "https://qnh.meituan.com",
            "store_whitelist": [],
            "store_blacklist": [],
            "data_paths": {
                "1": {"enabled": True, "name": "订单API"},
                "2": {"enabled": True, "name": "门店经营指标"},
                "3": {"enabled": True, "name": "门店流量"},
                "4": {"enabled": True, "name": "商品流量"},
                "5": {"enabled": True, "name": "品类分析"},
                "6": {"enabled": True, "name": "门店在售商品"},
            },
            "history_days": 7,
            "collect_time": "14:30",
        }
        self.step_results = {}

    def run(self):
        """运行完整安装向导"""
        print_banner("牵牛花数据采集 Skill · 安装向导")
        print("本向导将引导你完成 7 步配置，预计需要 10-15 分钟。")
        print("过程中需要你配合：启动浏览器、登录牵牛花中台、确认门店范围。\n")

        if not confirm("是否开始安装？", default=True):
            print("安装已取消。")
            return False

        steps = [
            ("环境检测", self.step1_env_check),
            ("数据目录配置", self.step2_data_dir),
            ("浏览器与登录", self.step3_browser_login),
            ("品牌范围确认", self.step4_brand_confirm),
            ("采集路径验证", self.step5_path_verify),
            ("历史数据初始化", self.step6_history_init),
            ("配置完成", self.step7_finalize),
        ]

        total = len(steps)
        for i, (name, func) in enumerate(steps, 1):
            print_step(i, total, name)
            try:
                result = func()
                self.step_results[name] = result
                if result is False:
                    print(f"\n⚠️  步骤「{name}」未完成，安装中止。")
                    print("你可以稍后重新运行本向导继续。")
                    return False
            except Exception as e:
                logging.error(f"步骤「{name}」执行出错: {e}", exc_info=True)
                print(f"\n❌ 步骤「{name}」执行出错: {e}")
                if not confirm("是否继续下一步？", default=False):
                    return False

        print_banner("安装完成")
        print(f"配置文件已保存到: {CONFIG_FILE}")
        print("\n下一步：")
        print("  1. 运行采集测试: python3 scripts/run_collector.py --dry-run")
        print("  2. 创建定时任务（每日14:30）: 使用 doubao-cron-scheduler")
        print("  3. 查看数据路径说明: references/data_paths.md")
        return True

    # ============================================================
    # Step 1: 环境检测
    # ============================================================

    def step1_env_check(self):
        """检测运行环境"""
        print("正在检测运行环境...\n")

        checks = []

        # Python 版本
        py_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        py_ok = sys.version_info >= (3, 8)
        checks.append(("Python 版本", py_version, py_ok, "≥ 3.8"))

        # 必需包检测
        required_packages = ["requests", "openpyxl", "pandas"]
        missing = []
        for pkg in required_packages:
            try:
                __import__(pkg)
            except ImportError:
                missing.append(pkg)
        checks.append(("必需 Python 包", "全部已安装" if not missing else f"缺失: {', '.join(missing)}", not missing, "requests/openpyxl/pandas"))

        # Chrome 远程调试端口检测
        chrome_available = self._check_chrome_port(self.config["chrome_port"])
        checks.append(("Chrome 远程调试", f"端口 {self.config['chrome_port']} {'可用' if chrome_available else '不可用'}", chrome_available, "浏览器需开启远程调试"))

        # 网络连通性
        network_ok = self._check_network()
        checks.append(("网络连通性", "可访问牵牛花中台" if network_ok else "无法访问", network_ok, "能访问 qnh.meituan.com"))

        # 打印检测结果
        all_passed = True
        for name, value, passed, requirement in checks:
            status = "✅" if passed else "❌"
            print(f"  {status} {name}: {value}")
            if not passed:
                all_passed = False
                print(f"     要求: {requirement}")

        print()

        if not all_passed:
            print("部分环境检测未通过。")
            if missing:
                if confirm(f"是否自动安装缺失的 Python 包 ({', '.join(missing)})？", default=True):
                    self._install_packages(missing)
            if not chrome_available:
                print("\n请手动启动 Chrome 浏览器（开启远程调试端口）：")
                print(f"  Linux: google-chrome --remote-debugging-port={self.config['chrome_port']} --user-data-dir=/home/user/qianniu-chrome-profile")
                print(f"  Windows: \"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe\" --remote-debugging-port=9333 --user-data-dir=\"C:\\qianniu-chrome-profile\"")
                input("启动完成后按回车继续...")
            if not network_ok:
                print("请检查网络连接后重新运行安装向导。")
                return False

        print("✅ 环境检测通过")
        return True

    def _check_chrome_port(self, port):
        """检查 Chrome 远程调试端口"""
        import urllib.request
        try:
            url = f"http://127.0.0.1:{port}/json/version"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=3) as resp:
                return True
        except Exception:
            return False

    def _check_network(self):
        """检查网络连通性"""
        import urllib.request
        try:
            req = urllib.request.Request("https://qnh.meituan.com", method="HEAD")
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status < 500
        except Exception:
            # 即使 HEAD 请求失败，也可能是因为需要登录，不视为网络不通
            try:
                req = urllib.request.Request("https://www.meituan.com", method="HEAD")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return resp.status < 500
            except Exception:
                return False

    def _install_packages(self, packages):
        """安装缺失的 Python 包"""
        import subprocess
        print(f"\n正在安装: {' '.join(packages)}")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install"] + packages)
            print("✅ 安装完成")
        except Exception as e:
            print(f"❌ 安装失败: {e}")
            print("请手动安装后重新运行向导。")

    # ============================================================
    # Step 2: 数据目录配置
    # ============================================================

    def step2_data_dir(self):
        """配置数据目录"""
        default_dir = str(get_skill_dir() / "data")
        data_dir = ask_input("请输入数据存储目录", default=default_dir)

        try:
            data_path = Path(data_dir).expanduser().resolve()
            ensure_dir(data_path)
            # 测试写入权限
            test_file = data_path / ".write_test"
            test_file.write_text("test")
            test_file.unlink()
            self.config["data_dir"] = str(data_path)
            print(f"✅ 数据目录已设置: {data_path}")
            print(f"   原始数据: {data_path / 'raw'}")
            print(f"   累积总表: {data_path / 'master'}")
            print(f"   日志文件: {data_path / 'logs'}")
            return True
        except Exception as e:
            print(f"❌ 数据目录设置失败: {e}")
            return False

    # ============================================================
    # Step 3: 浏览器与登录
    # ============================================================

    def step3_browser_login(self):
        """配置浏览器并引导登录"""
        # 检测操作系统，设置默认端口
        if sys.platform == "win32":
            default_port = 9333
        else:
            default_port = 9222

        port = ask_input("Chrome 远程调试端口", default=str(default_port))
        try:
            self.config["chrome_port"] = int(port)
        except ValueError:
            print("❌ 端口号必须是数字")
            return False

        base_url = ask_input("牵牛花中台地址", default=self.config["base_url"])
        self.config["base_url"] = base_url

        print(f"\n请确保 Chrome 浏览器已启动（端口 {self.config['chrome_port']}），")
        print(f"并在浏览器中打开 {base_url} 完成登录。\n")

        input("登录完成后按回车继续...")

        # 验证登录态
        print("正在验证登录态...")
        try:
            from check_login import run_check
            is_logged_in, message = run_check(verbose=True)
            if is_logged_in:
                print(f"✅ {message}")
                return True
            else:
                print(f"❌ 登录验证失败: {message}")
                if confirm("是否跳过登录验证，稍后手动验证？", default=False):
                    print("⚠️  已跳过登录验证，采集前请确保已登录。")
                    return True
                return False
        except Exception as e:
            print(f"⚠️  登录验证脚本执行异常: {e}")
            print("将跳过自动验证，请确保浏览器已登录。")
            return True

    # ============================================================
    # Step 4: 品牌范围确认
    # ============================================================

    def step4_brand_confirm(self):
        """确认 F.LUX 品牌门店范围"""
        print("本 Skill 为 F.LUX 品牌专用，将自动过滤非 F.LUX 门店。\n")
        print(f"白名单关键词: {', '.join(FLUX_KEYWORDS)}")
        print(f"黑名单关键词: {', '.join(EXCLUDE_KEYWORDS)}")
        print()

        # 尝试从浏览器获取门店列表（如果已登录）
        stores = self._fetch_store_list()
        if stores:
            flux_stores = filter_flux_stores(stores)
            other_stores = [s for s in stores if s not in flux_stores]

            print(f"检测到 {len(stores)} 家门店：")
            print(f"  F.LUX 门店: {len(flux_stores)} 家")
            print(f"  其他门店: {len(other_stores)} 家")
            if other_stores:
                print(f"  将被剔除: {', '.join(s.get('name','') for s in other_stores[:5])}")
                if len(other_stores) > 5:
                    print(f"  ... 等共 {len(other_stores)} 家")

            self.config["store_whitelist"] = [s.get("name") for s in flux_stores]
            self.config["store_blacklist"] = [s.get("name") for s in other_stores]
        else:
            print("未能自动获取门店列表，将使用关键词自动过滤。")
            print("（采集时会自动过滤名称含 F.LUX/F·LUX 的门店）")

        print()
        if confirm("确认品牌范围为 F.LUX 门店（自动剔除觅初等跨品牌门店）？", default=True):
            print("✅ 品牌范围已确认")
            return True
        else:
            print("❌ 品牌范围未确认，无法继续。")
            return False

    def _fetch_store_list(self):
        """从浏览器获取门店列表（占位，实际由浏览器操作模块实现）"""
        # 实际实现需要通过 CDP 连接浏览器，在牵牛花页面执行 JS 获取门店列表
        # 这里返回空列表，表示未能自动获取
        return []

    # ============================================================
    # Step 5: 采集路径验证
    # ============================================================

    def step5_path_verify(self):
        """验证 6 条数据路径"""
        print("将用最小数据量验证 6 条数据路径。\n")
        print("注意：路径验证需要浏览器已登录牵牛花中台。")
        print("验证过程中如遇页面弹窗，请手动处理。\n")

        if not confirm("是否开始验证？", default=True):
            print("已跳过路径验证，稍后可手动运行测试。")
            return True

        paths = [
            (1, "订单API", "API 调用，无需页面操作"),
            (2, "门店经营指标", "首页→门店明细导出"),
            (3, "门店流量", "流量概览→门店流量排行导出"),
            (4, "商品流量（三Tab）", "商品流量分析导出（全部/店外/店内）"),
            (5, "品类分析", "品类分析导出（需先选指标）"),
            (6, "门店在售商品", "门店商品页读取（逐店遍历，较慢）"),
        ]

        results = {}
        for path_id, name, desc in paths:
            print(f"\n  验证路径 {path_id}: {name}")
            print(f"    方式: {desc}")
            # 实际验证由浏览器操作模块执行
            # 这里标记为待验证
            print(f"    ⏳ 待浏览器操作模块实现后自动验证")
            results[path_id] = "pending"

        print("\n✅ 路径配置完成（实际验证将在首次采集时执行）")
        print("   如某条路径采集失败，可使用单表补采脚本重试。")
        return True

    # ============================================================
    # Step 6: 历史数据初始化
    # ============================================================

    def step6_history_init(self):
        """历史数据初始化"""
        print("选择历史数据回溯范围：\n")
        print("  1. 不回溯（从今天开始采集）")
        print("  2. 回溯近 7 天（推荐，可支撑近7日基线计算）")
        print("  3. 回溯近 30 天（完整历史库，耗时较长）")
        print("  4. 从备份文件迁移")
        print()

        choice = ask_input("请选择", default="2")

        if choice == "1":
            self.config["history_days"] = 0
            print("✅ 不回溯历史数据")
        elif choice == "2":
            self.config["history_days"] = 7
            print("✅ 将回溯近 7 天数据（安装完成后可手动执行）")
        elif choice == "3":
            self.config["history_days"] = 30
            print("✅ 将回溯近 30 天数据（注意：流量数据仅近2周可用）")
        elif choice == "4":
            backup_file = ask_input("请输入备份文件路径（Excel/CSV）")
            if Path(backup_file).exists():
                self.config["backup_file"] = backup_file
                print("✅ 备份文件已记录，安装完成后执行迁移")
            else:
                print(f"❌ 文件不存在: {backup_file}")
                return False
        else:
            print("❌ 无效选择")
            return False

        print("\n⚠️  历史回溯将在安装完成后手动执行，以避免向导耗时过长。")
        print("   执行命令: python3 scripts/run_collector.py --backfill")
        return True

    # ============================================================
    # Step 7: 配置完成
    # ============================================================

    def step7_finalize(self):
        """保存配置并完成安装"""
        from datetime import datetime
        self.config["setup_date"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 保存配置
        save_config(self.config)

        # 创建数据目录结构
        data_dir = Path(self.config["data_dir"])
        for subdir in ["raw", "master/by_table", "logs"]:
            ensure_dir(data_dir / subdir)

        print("\n配置摘要：")
        print(f"  数据目录: {self.config['data_dir']}")
        print(f"  Chrome 端口: {self.config['chrome_port']}")
        print(f"  中台地址: {self.config['base_url']}")
        print(f"  品牌范围: F.LUX 门店（自动过滤）")
        print(f"  采集路径: 6 条全部启用")
        print(f"  历史回溯: {self.config['history_days']} 天")
        print()

        return True

# ============================================================
# 命令行入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="牵牛花数据采集 Skill 安装向导")
    parser.add_argument("--check", action="store_true", help="仅检查配置状态")
    parser.add_argument("--show", action="store_true", help="显示当前配置")
    parser.add_argument("--reset", action="store_true", help="重置配置（删除现有配置）")
    args = parser.parse_args()

    setup_logging()

    if args.check:
        if is_configured():
            print("✅ 已完成配置")
            sys.exit(0)
        else:
            print("❌ 未配置")
            sys.exit(1)

    if args.show:
        config = load_config()
        if config:
            print(json.dumps(config, ensure_ascii=False, indent=2))
        else:
            print("未找到配置文件")
        sys.exit(0)

    if args.reset:
        if CONFIG_FILE.exists():
            CONFIG_FILE.unlink()
            print("✅ 配置已重置")
        else:
            print("配置文件不存在")
        sys.exit(0)

    # 正常安装流程
    if is_configured():
        print_banner("检测到已有配置")
        if not confirm("是否覆盖现有配置重新安装？", default=False):
            print("保留现有配置，安装取消。")
            return

    wizard = SetupWizard()
    success = wizard.run()
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
