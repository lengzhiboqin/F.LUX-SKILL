#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""闪购数据管线 - 环境检测工具。

跨平台(Linux/macOS/Windows/WSL/Git Bash)、跨agent。
检测：OS、Python、Playwright、浏览器CDP、飞书工具、agent类型。

用法：
  python3 env_check.py              # JSON输出
  python3 env_check.py --human      # 人类可读
"""

import warnings
warnings.filterwarnings("ignore")

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path


def run_cmd(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.returncode
    except Exception:
        return "", -1


def port_open(host, port, timeout=2):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False


def detect_os():
    system = platform.system()
    info = {
        "type": system,
        "type_display": {"Linux": "Linux", "Darwin": "macOS", "Windows": "Windows"}.get(system, system),
        "release": platform.release(),
        "machine": platform.machine(),
        "is_wsl": False,
        "is_git_bash": False,
        "home": str(Path.home()),
        "path_sep": os.sep,
    }
    if system == "Linux":
        try:
            with open("/proc/version") as f:
                if "microsoft" in f.read().lower():
                    info["is_wsl"] = True
        except Exception:
            pass
    if os.environ.get("MSYSTEM") or os.environ.get("MINGW_PREFIX"):
        info["is_git_bash"] = True
    return info


def detect_python():
    info = {
        "version": platform.python_version(),
        "version_major_minor": f"{sys.version_info.major}.{sys.version_info.minor}",
        "executable": sys.executable,
        "commands": {},
        "pip": None,
    }
    for cmd in ["python3", "python", "py"]:
        path = shutil.which(cmd)
        if path:
            out, rc = run_cmd([cmd, "--version"])
            info["commands"][cmd] = {"path": path, "version": out}
    for pip_cmd in ["pip3", "pip"]:
        if shutil.which(pip_cmd):
            info["pip"] = pip_cmd
            break
    return info


def detect_python_packages():
    packages = {}
    for pkg in ["playwright", "openpyxl", "pandas", "requests"]:
        try:
            mod = __import__(pkg)
            packages[pkg] = getattr(mod, "__version__", "installed")
        except ImportError:
            packages[pkg] = None
    return packages


def detect_cdp():
    info = {"ports": {}, "available_port": None, "browser_info": None}
    for port in [9222, 9223, 9224, 9333]:
        entry = {"available": False}
        if port_open("127.0.0.1", port):
            try:
                resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3)
                data = json.loads(resp.read())
                entry["available"] = True
                entry["browser"] = data.get("Browser", "unknown")
            except Exception:
                entry["available"] = True
                entry["browser"] = "port open"
        info["ports"][str(port)] = entry
        if entry["available"] and info["available_port"] is None:
            info["available_port"] = port
            info["browser_info"] = entry.get("browser", "")
    return info


def detect_feishu():
    info = {
        "lark_cli": {"installed": False, "path": None, "logged_in": False},
        "api_config": {"available": False, "config_file": None, "has_app_id": False, "has_app_secret": False},
    }
    lark_path = shutil.which("lark-cli")
    if lark_path:
        info["lark_cli"]["installed"] = True
        info["lark_cli"]["path"] = lark_path
        out, rc = run_cmd(["lark-cli", "auth", "status"], timeout=10)
        info["lark_cli"]["logged_in"] = (rc == 0)
    candidates = [
        Path.home() / ".config" / "shangou-pipeline" / "feishu.json",
        Path.home() / ".config" / "shangou-workflow" / "feishu.json",
    ]
    for p in candidates:
        if p.exists():
            try:
                cfg = json.loads(p.read_text(encoding="utf-8"))
                info["api_config"]["config_file"] = str(p)
                info["api_config"]["has_app_id"] = bool(cfg.get("app_id"))
                info["api_config"]["has_app_secret"] = bool(cfg.get("app_secret"))
                info["api_config"]["available"] = (
                    info["api_config"]["has_app_id"] and info["api_config"]["has_app_secret"]
                )
            except Exception:
                pass
    return info


def detect_agent():
    info = {"type": "unknown", "confidence": "low", "signals": []}
    agent_signals = {
        "doubao": ["DOUBAO", "ARK_API", "VOLC_ARK"],
        "workbuddy": ["WORKBUDDY", "WUKONG", "BUDDY"],
        "codex": ["CODEX", "OPENAI_API"],
        "hermes": ["HERMES"],
        "claude": ["ANTHROPIC", "CLAUDE"],
    }
    env_keys = " ".join(os.environ.keys()).upper()
    for agent, keywords in agent_signals.items():
        for kw in keywords:
            if kw in env_keys:
                info["signals"].append(kw)
                if info["type"] == "unknown":
                    info["type"] = agent
                    info["confidence"] = "medium"
    skill_paths = [
        (Path.home() / ".doubao" / "agent_mode" / "workspace" / ".user_skills", "doubao"),
        (Path.home() / ".codex" / "skills", "codex"),
        (Path.home() / ".claude" / "skills", "claude"),
    ]
    for sp, agent_type in skill_paths:
        if sp.exists():
            info["type"] = agent_type
            info["confidence"] = "high"
            info["signals"].append(f"skill_dir:{agent_type}")
    return info


def run_full_check():
    return {
        "os": detect_os(),
        "python": detect_python(),
        "packages": detect_python_packages(),
        "cdp": detect_cdp(),
        "feishu": detect_feishu(),
        "agent": detect_agent(),
    }


def print_human(r):
    line = "=" * 62
    print(line)
    print("  闪购数据管线 · 环境检测报告")
    print(line)
    osi = r["os"]
    print(f"\n▸ 操作系统: {osi['type_display']} {osi['release']} ({osi['machine']})")
    if osi["is_wsl"]:
        print("  ⚠ WSL环境，浏览器需在Windows侧启动并开放CDP端口")
    if osi["is_git_bash"]:
        print("  ⚠ Git Bash环境，路径建议用引号包裹")
    py = r["python"]
    print(f"\n▸ Python: {py['version']}")
    print(f"  解释器: {py['executable']}")
    print(f"\n▸ Python依赖:")
    for pkg, ver in r["packages"].items():
        print(f"  {'✓' if ver else '✗'} {pkg}: {ver if ver else '未安装'}")
    cdp = r["cdp"]
    print(f"\n▸ 浏览器CDP:")
    for port, e in cdp["ports"].items():
        if e["available"]:
            print(f"  ✓ 端口 {port}: {e.get('browser', '可用')}")
        else:
            print(f"  ✗ 端口 {port}: 不可用")
    if cdp["available_port"]:
        print(f"  → 使用端口 {cdp['available_port']}")
    else:
        print("  → 未检测到CDP，需启动带 --remote-debugging-port 的Chrome/Chromium")
    fs = r["feishu"]
    print(f"\n▸ 飞书集成:")
    lc = fs["lark_cli"]
    if lc["installed"]:
        print(f"  {'✓' if lc['logged_in'] else '⚠'} lark-cli: {'已登录' if lc['logged_in'] else '未登录'}")
    else:
        print(f"  ✗ lark-cli: 未安装")
    if fs["api_config"]["available"]:
        print(f"  ✓ 飞书自建应用API: 已配置")
    else:
        print(f"  · 飞书自建应用API: 未配置(可选)")
    ag = r["agent"]
    print(f"\n▸ Agent环境: {ag['type']} (置信度:{ag['confidence']})")
    print(line)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--human", action="store_true")
    args = ap.parse_args()
    result = run_full_check()
    if args.human:
        print_human(result)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
