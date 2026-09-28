#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
牵牛花数据采集 Skill - 公共工具模块
包含：配置管理、日志、文件操作、日期工具、通用函数
"""

import os
import sys
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

# ============================================================
# 常量定义
# ============================================================

SKILL_NAME = "qianniu-data-collector"
CONFIG_DIR = Path.home() / ".config" / "qianniu-collector"
CONFIG_FILE = CONFIG_DIR / "config.json"

# 6条数据路径编号与名称
DATA_PATHS = {
    1: "订单API",
    2: "门店经营指标",
    3: "门店流量",
    4: "商品流量",
    5: "品类分析",
    6: "门店在售商品",
}

# F.LUX 品牌门店名称关键词（白名单过滤）
FLUX_KEYWORDS = ["F.LUX", "F·LUX", "f.lux", "f·lux"]

# 跨品牌门店（需剔除）
EXCLUDE_KEYWORDS = ["觅初"]

# ============================================================
# 配置管理
# ============================================================

def load_config():
    """加载配置文件，不存在则返回 None"""
    if not CONFIG_FILE.exists():
        return None
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"配置文件读取失败: {e}")
        return None

def save_config(config):
    """保存配置文件"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    logging.info(f"配置已保存到 {CONFIG_FILE}")

def is_configured():
    """检查是否已完成配置"""
    config = load_config()
    if config is None:
        return False
    required_keys = ["data_dir", "chrome_port", "store_whitelist"]
    return all(k in config for k in required_keys)

def get_skill_dir():
    """获取 Skill 根目录（脚本所在目录的上一级）"""
    return Path(__file__).resolve().parent.parent

def get_data_dir():
    """获取数据目录，优先从配置读取，否则用默认值"""
    config = load_config()
    if config and "data_dir" in config:
        return Path(config["data_dir"])
    return get_skill_dir() / "data"

# ============================================================
# 日志
# ============================================================

def setup_logging(log_dir=None, level=logging.INFO):
    """配置日志，同时输出到控制台和文件"""
    if log_dir is None:
        log_dir = get_data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / f"collect_{datetime.now().strftime('%Y%m%d')}.log"

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # 控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # 文件处理器
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    return root_logger

# ============================================================
# 日期工具
# ============================================================

def get_yesterday():
    """获取昨日日期（YYYY-MM-DD）"""
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

def get_today():
    """获取今日日期（YYYY-MM-DD）"""
    return datetime.now().strftime("%Y-%m-%d")

def get_date_range(days):
    """获取最近 N 天的日期列表（含今日，从旧到新）"""
    today = datetime.now().date()
    return [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days-1, -1, -1)]

def parse_date(date_str):
    """解析日期字符串为 date 对象"""
    return datetime.strptime(date_str, "%Y-%m-%d").date()

def to_ms_timestamp(date_str, end_of_day=False):
    """日期字符串转毫秒时间戳（北京时间 +08:00）"""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    # 转换为北京时间的时间戳
    # 注意：这里假设系统运行在东八区，如需跨时区需调整
    return int(dt.timestamp() * 1000)

# ============================================================
# 文件操作
# ============================================================

def ensure_dir(path):
    """确保目录存在"""
    Path(path).mkdir(parents=True, exist_ok=True)

def get_raw_dir(date_str):
    """获取指定日期的原始数据目录"""
    raw_dir = get_data_dir() / "raw" / date_str
    ensure_dir(raw_dir)
    return raw_dir

def get_master_dir():
    """获取总表目录"""
    master_dir = get_data_dir() / "master" / "by_table"
    ensure_dir(master_dir)
    return master_dir

def save_json(data, filepath):
    """保存 JSON 文件"""
    ensure_dir(Path(filepath).parent)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_json(filepath):
    """加载 JSON 文件"""
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)

def save_result_summary(summary):
    """保存最近一次采集的结构化结果"""
    master_dir = get_data_dir() / "master"
    ensure_dir(master_dir)
    summary_file = master_dir / "last_collect.json"
    save_json(summary, summary_file)
    return summary_file

# ============================================================
# 门店过滤
# ============================================================

def is_flux_store(store_name):
    """判断是否为 F.LUX 门店（白名单过滤）"""
    if not store_name:
        return False
    # 先排除跨品牌门店
    for kw in EXCLUDE_KEYWORDS:
        if kw in store_name:
            return False
    # 再匹配 F.LUX 关键词
    for kw in FLUX_KEYWORDS:
        if kw.lower() in store_name.lower():
            return True
    return False

def filter_flux_stores(store_list):
    """从门店列表中过滤出 F.LUX 门店"""
    return [s for s in store_list if is_flux_store(s.get("name", ""))]

# ============================================================
# 通用工具
# ============================================================

def print_banner(text):
    """打印分隔横幅"""
    print("\n" + "=" * 60)
    print(f"  {text}")
    print("=" * 60 + "\n")

def print_step(step_num, total, title):
    """打印步骤信息"""
    print(f"\n[{step_num}/{total}] {title}")
    print("-" * 40)

def confirm(prompt, default=False):
    """交互式确认"""
    suffix = " [Y/n] " if default else " [y/N] "
    while True:
        try:
            answer = input(prompt + suffix).strip().lower()
        except EOFError:
            return default
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("请输入 y 或 n")

def ask_input(prompt, default=None):
    """交互式输入，有默认值"""
    if default is not None:
        prompt = f"{prompt} [默认: {default}] "
    while True:
        try:
            answer = input(prompt).strip()
        except EOFError:
            return default
        if not answer and default is not None:
            return default
        if answer:
            return answer

def retry(func, max_retries=2, delay=1, *args, **kwargs):
    """重试执行函数"""
    import time
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                logging.warning(f"第 {attempt+1} 次尝试失败: {e}，{delay}秒后重试...")
                time.sleep(delay)
            else:
                logging.error(f"重试 {max_retries} 次后仍失败: {e}")
    raise last_error

# ============================================================
# 主入口（用于测试）
# ============================================================

if __name__ == "__main__":
    print("牵牛花数据采集 Skill - 公共工具模块")
    print(f"Skill 目录: {get_skill_dir()}")
    print(f"数据目录: {get_data_dir()}")
    print(f"已配置: {is_configured()}")
    print(f"昨日: {get_yesterday()}")
    print(f"今日: {get_today()}")
