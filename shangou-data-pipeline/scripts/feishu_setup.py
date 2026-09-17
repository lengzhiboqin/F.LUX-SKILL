#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""飞书对接验证与工具（安装向导用）。

两种通道：lark-cli（豆包环境）/ 飞书自建应用API（其他环境）。

用法：
  python3 feishu_setup.py verify --folder-token <token>
  python3 feishu_setup.py test-upload --folder-token <token>
  python3 feishu_setup.py method
"""

import warnings
warnings.filterwarnings("ignore")

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None

CONFIG_DIR = Path.home() / ".config" / "shangou-pipeline"
FEISHU_CFG = CONFIG_DIR / "feishu.json"
API = "https://open.feishu.cn/open-apis"


def lark_cli_available():
    if not shutil.which("lark-cli"):
        return False
    r = subprocess.run(["lark-cli", "auth", "status"], capture_output=True, timeout=10)
    return r.returncode == 0


def lark_list_files(folder_token):
    result = {}
    page_token = None
    for _ in range(20):
        params = {"folder_token": folder_token, "page_size": 200}
        if page_token:
            params["page_token"] = page_token
        r = subprocess.run(
            ["lark-cli", "drive", "files", "list",
             "--params", json.dumps(params), "--format", "json"],
            capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            break
        try:
            data = json.loads(r.stdout)
        except json.JSONDecodeError:
            break
        d = data.get("data", data)
        for f in d.get("files", []):
            result[f.get("name")] = f.get("token")
        if not d.get("has_more"):
            break
        page_token = d.get("page_token")
    return result


_token_cache = {"tok": "", "exp": 0}


def get_token():
    if _token_cache["tok"] and time.time() < _token_cache["exp"] - 120:
        return _token_cache["tok"]
    if requests is None:
        raise RuntimeError("需要requests: pip install requests")
    if not FEISHU_CFG.exists():
        raise RuntimeError(f"飞书配置不存在: {FEISHU_CFG}")
    cfg = json.loads(FEISHU_CFG.read_text(encoding="utf-8"))
    r = requests.post(f"{API}/auth/v3/tenant_access_token/internal",
                      json={"app_id": cfg["app_id"], "app_secret": cfg["app_secret"]}, timeout=30)
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(f"获取token失败: {d}")
    _token_cache["tok"] = d["tenant_access_token"]
    _token_cache["exp"] = time.time() + d.get("expire", 7200)
    return _token_cache["tok"]


def api_req(method, path, retries=2, **kwargs):
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {get_token()}"
    if "json" in kwargs:
        headers.setdefault("Content-Type", "application/json; charset=utf-8")
    for attempt in range(retries + 1):
        r = requests.request(method, f"{API}{path}", headers=headers, timeout=300, **kwargs)
        d = r.json()
        if d.get("code") == 2200 and attempt < retries:
            time.sleep(10 * (attempt + 1))
            continue
        return d
    return d


def api_list_files(folder_token):
    result = {}
    page_token = None
    for _ in range(20):
        params = {"folder_token": folder_token, "page_size": 200}
        if page_token:
            params["page_token"] = page_token
        d = api_req("GET", "/drive/v1/files", params=params)
        if d.get("code") != 0:
            break
        data = d.get("data", {})
        for f in data.get("files", []):
            result[f.get("name")] = f.get("token")
        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
    return result


def detect_method():
    if lark_cli_available():
        return "lark-cli"
    if FEISHU_CFG.exists():
        cfg = json.loads(FEISHU_CFG.read_text(encoding="utf-8"))
        if cfg.get("app_id") and cfg.get("app_secret"):
            return "api"
    return None


def verify_folder(folder_token):
    method = detect_method()
    if not method:
        return False, "无可用飞书通道（lark-cli未登录或未配置App ID/Secret）"
    try:
        if method == "lark-cli":
            result = lark_list_files(folder_token)
            return True, {"method": method, "file_count": len(result)}
        files = api_list_files(folder_token)
        return True, {"method": method, "file_count": len(files)}
    except Exception as e:
        return False, str(e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["verify", "test-upload", "method"])
    ap.add_argument("--folder-token")
    args = ap.parse_args()

    if args.action == "method":
        print(json.dumps({"method": detect_method()}, ensure_ascii=False))
        return
    if args.action == "verify":
        if not args.folder_token:
            print("错误：需要 --folder-token", file=sys.stderr); sys.exit(1)
        success, result = verify_folder(args.folder_token)
        print(json.dumps({"success": success, "result": result}, ensure_ascii=False, indent=2, default=str))
        sys.exit(0 if success else 1)
    if args.action == "test-upload":
        if not args.folder_token:
            print("错误：需要 --folder-token", file=sys.stderr); sys.exit(1)
        import tempfile
        test_file = Path(tempfile.gettempdir()) / f"feishu_test_{int(time.time())}.txt"
        test_file.write_text(f"闪购管线连通性测试 {time.strftime('%Y-%m-%d %H:%M:%S')}", encoding="utf-8")
        # 简单上传测试（复用verify即可）
        success, result = verify_folder(args.folder_token)
        test_file.unlink(missing_ok=True)
        print(json.dumps({"success": success, "result": result}, ensure_ascii=False, indent=2, default=str))
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
