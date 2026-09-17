#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""整合后飞书云空间同步：上传Excel总表（可选备份原始CSV）。

两种通道（自动选择，优先 lark-cli）：
  1. lark-cli（豆包环境）：同名文件用 --file-token 原地覆盖
  2. 飞书自建应用 API（workbuddy/codex等）：同名文件删除后重传

配置来源（按优先级）：
  ~/.config/shangou-pipeline/config.json 的 feishu 字段
  ~/.config/shangou-integrator/config.json（旧版兼容）
  ~/.config/shangou-workflow/config.json（旧版兼容）
飞书App凭据：~/.config/shangou-workflow/feishu.json

用法：
  python3 feishu_sync.py                     # 按配置上传Excel总表
  python3 feishu_sync.py --excel <path>      # 指定Excel文件
  python3 feishu_sync.py --raw-day <日期>     # 额外备份某日原始CSV
  python3 feishu_sync.py --dry-run           # 只打印不实际上传
"""

import argparse
import glob
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

API = "https://open.feishu.cn/open-apis"
PIPELINE_CFG = Path.home() / ".config" / "shangou-pipeline" / "config.json"
LEGACY_INTEGRATOR_CFG = Path.home() / ".config" / "shangou-integrator" / "config.json"
LEGACY_WORKFLOW_CFG = Path.home() / ".config" / "shangou-workflow" / "config.json"
FEISHU_KEYS = Path.home() / ".config" / "shangou-workflow" / "feishu.json"

EXCEL_NAME = "F.LUX品牌数据汇总表.xlsx"


def log(msg):
    print(f"[feishu] {msg}", flush=True)


def load_config():
    """加载飞书配置（新管线配置优先，回退旧版配置）"""
    cfg = {}
    # 新管线配置
    if PIPELINE_CFG.exists():
        p = json.loads(PIPELINE_CFG.read_text(encoding="utf-8"))
        cfg["dirs"] = {
            "raw_dir": p.get("raw_dir"),
            "master_dir": p.get("master_dir"),
        }
        if p.get("feishu"):
            cfg["feishu"] = p["feishu"]
    # 旧版兼容回退
    if LEGACY_WORKFLOW_CFG.exists():
        wf = json.loads(LEGACY_WORKFLOW_CFG.read_text(encoding="utf-8"))
        if not cfg.get("dirs", {}).get("raw_dir"):
            cfg["dirs"] = cfg.get("dirs", {})
            cfg["dirs"]["raw_dir"] = wf.get("dirs", {}).get("raw_dir")
            cfg["dirs"]["master_dir"] = wf.get("dirs", {}).get("master_dir")
        if not cfg.get("feishu"):
            cfg["feishu"] = wf.get("feishu", {})
    if LEGACY_INTEGRATOR_CFG.exists():
        integ = json.loads(LEGACY_INTEGRATOR_CFG.read_text(encoding="utf-8"))
        if integ.get("raw_dir"):
            cfg.setdefault("dirs", {})["raw_dir"] = integ.get("raw_dir")
        if integ.get("master_dir"):
            cfg.setdefault("dirs", {})["master_dir"] = integ.get("master_dir")
        if integ.get("feishu"):
            cfg["feishu"] = integ["feishu"]
    return cfg


# ── lark-cli 通道 ─────────────────────────────────────────

def lark_available():
    """检测lark-cli是否可用且已登录。

    优先用 auth status（若当前CLI版本支持）；部分环境CLI无auth子命令
    （直接报 unknown command），此时退化为实际调用 drive files list
    探测登录态，避免误报"未登录"。
    """
    if not shutil.which("lark-cli"):
        return False
    r = subprocess.run(["lark-cli", "auth", "status"], capture_output=True, timeout=10)
    if r.returncode == 0:
        return True
    # auth子命令不可用：用真实读接口探测（能列出文件即视为已登录）
    try:
        probe = subprocess.run(
            ["lark-cli", "drive", "files", "list",
             "--params", json.dumps({"page_size": 1}), "--format", "json"],
            capture_output=True, text=True, timeout=15,
        )
        if probe.returncode != 0:
            return False
        data = json.loads(probe.stdout)
        d = data.get("data", data)
        return data.get("ok") is True or "files" in d
    except Exception:
        return False


def lark_list_files(folder_token):
    """列出文件夹文件，返回 {name: file_token}"""
    result = {}
    page_token = None
    for _ in range(20):  # 最多20页
        params = {"folder_token": folder_token, "page_size": 200}
        if page_token:
            params["page_token"] = page_token
        r = subprocess.run(
            ["lark-cli", "drive", "files", "list",
             "--params", json.dumps(params), "--format", "json"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            break
        try:
            data = json.loads(r.stdout)
        except json.JSONDecodeError:
            break
        # 兼容不同返回结构
        d = data.get("data", data)
        files = d.get("files", [])
        for f in files:
            result[f.get("name")] = f.get("token")
        if not d.get("has_more"):
            break
        page_token = d.get("page_token")
    return result


def lark_upload(folder_token, file_path, name=None):
    """lark-cli上传，同名文件原地覆盖"""
    file_path = str(file_path)
    name = name or os.path.basename(file_path)
    existing = lark_list_files(folder_token)
    file_token = existing.get(name)

    cmd = ["lark-cli", "drive", "+upload", "--file", file_path,
           "--name", name, "--format", "json"]
    if file_token:
        cmd += ["--file-token", file_token]
        log(f"覆盖已有文件: {name}")
    else:
        cmd += ["--folder-token", folder_token]
        log(f"上传新文件: {name}")

    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        return False, r.stderr[:300], None
    try:
        data = json.loads(r.stdout)
        token = (data.get("data", {}) or {}).get("file_token", file_token)
        return True, "ok", token
    except json.JSONDecodeError:
        return True, r.stdout[:200], file_token


# ── 飞书API通道 ──────────────────────────────────────────

_tok = {"v": "", "exp": 0}


def api_token():
    if _tok["v"] and time.time() < _tok["exp"] - 120:
        return _tok["v"]
    if requests is None:
        raise RuntimeError("需要requests: pip install requests")
    if not FEISHU_KEYS.exists():
        raise RuntimeError(f"飞书凭据不存在: {FEISHU_KEYS}")
    keys = json.loads(FEISHU_KEYS.read_text(encoding="utf-8"))
    r = requests.post(f"{API}/auth/v3/tenant_access_token/internal",
                      json={"app_id": keys["app_id"], "app_secret": keys["app_secret"]},
                      timeout=30)
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(f"获取token失败: {d}")
    _tok["v"] = d["tenant_access_token"]
    _tok["exp"] = time.time() + d.get("expire", 7200)
    return _tok["v"]


def api_req(method, path, retries=2, **kw):
    headers = kw.pop("headers", {})
    headers["Authorization"] = f"Bearer {api_token()}"
    for attempt in range(retries + 1):
        r = requests.request(method, f"{API}{path}", headers=headers, timeout=300, **kw)
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


def api_delete(token):
    api_req("DELETE", f"/drive/v1/files/{token}", params={"type": "file"})


def api_upload(folder_token, file_path, name=None):
    """API上传，同名文件先删后传（保证文件夹内只一份）"""
    file_path = Path(file_path)
    name = name or file_path.name
    existing = api_list_files(folder_token)
    if name in existing:
        log(f"删除旧文件后重传: {name}")
        api_delete(existing[name])
        time.sleep(2)
    else:
        log(f"上传新文件: {name}")

    size = file_path.stat().st_size
    with open(file_path, "rb") as f:
        d = api_req("POST", "/drive/v1/medias/upload_all",
                    files={"file": (name, f)},
                    data={"file_name": name, "parent_type": "explorer",
                          "size": str(size), "parent_node": folder_token})
    if d.get("code") != 0:
        return False, json.dumps(d, ensure_ascii=False)[:300], None
    return True, "ok", d.get("data", {}).get("file_token")


# ── 统一入口 ─────────────────────────────────────────────

def upload(folder_token, file_path, name=None):
    if lark_available():
        return lark_upload(folder_token, file_path, name)
    if FEISHU_KEYS.exists():
        return api_upload(folder_token, file_path, name)
    return False, "无可用飞书通道（lark-cli未登录且未配置App凭据）", None


def sync_excel(cfg, excel_path=None, dry_run=False):
    """同步Excel总表"""
    feishu = cfg.get("feishu", {})
    if not feishu.get("enabled"):
        log("飞书同步未启用，跳过")
        return False
    folder_token = feishu.get("folder_token")
    if not folder_token:
        log("未配置folder_token，跳过")
        return False

    master_dir = cfg.get("dirs", {}).get("master_dir")
    if excel_path is None:
        excel_path = str(Path(master_dir) / EXCEL_NAME)
    excel_path = Path(excel_path)
    if not excel_path.exists():
        log(f"Excel文件不存在: {excel_path}")
        return False

    log(f"准备上传Excel总表 ({excel_path.stat().st_size // 1024}KB)")
    if dry_run:
        log(f"[dry-run] 将上传 {excel_path} → folder {folder_token}")
        return True

    ok, msg, token = upload(folder_token, excel_path, EXCEL_NAME)
    if ok:
        log(f"✓ Excel总表已同步到飞书")
        if token:
            log(f"  file_token: {token}")
    else:
        log(f"✗ 上传失败: {msg}")
    return ok


def sync_raw(cfg, day, dry_run=False):
    """备份某日原始CSV（打包zip上传）"""
    import zipfile
    feishu = cfg.get("feishu", {})
    if not feishu.get("enabled") or not feishu.get("sync_raw"):
        return False
    folder_token = feishu.get("folder_token")
    raw_dir = cfg.get("dirs", {}).get("raw_dir")
    day_dir = Path(raw_dir) / day
    if not day_dir.exists():
        log(f"原始数据目录不存在: {day_dir}")
        return False

    zip_path = Path("/tmp") / f"shangou_raw_{day}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for csv_f in day_dir.glob("*.csv"):
            zf.write(csv_f, csv_f.name)

    name = f"原始数据_{day}.zip"
    if dry_run:
        log(f"[dry-run] 将上传 {name}")
        return True
    ok, msg, _ = upload(folder_token, zip_path, name)
    zip_path.unlink(missing_ok=True)
    if ok:
        log(f"✓ 原始数据 {day} 已备份")
    return ok


def main():
    ap = argparse.ArgumentParser(description="整合后飞书同步")
    ap.add_argument("--excel", help="指定Excel文件路径")
    ap.add_argument("--raw-day", help="额外备份某日原始CSV(YYYY-MM-DD)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    feishu = cfg.get("feishu", {})
    if not feishu.get("enabled"):
        log("飞书同步未在安装配置中启用，跳过。可重新运行安装器启用。")
        sys.exit(0)

    results = []
    results.append(sync_excel(cfg, args.excel, args.dry_run))
    if args.raw_day:
        results.append(sync_raw(cfg, args.raw_day, args.dry_run))

    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
