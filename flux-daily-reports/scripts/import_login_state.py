# -*- coding: utf-8 -*-
"""登录态导入：把用户从本机导出的 Cookie/登录态写入云端 Chromium profile。

背景
----
阶段 1 原定的「云端 noVNC 人工登录」不可行（远程输入体验无法过滑块验证）。
经用户明确批准，改用「本机登录 → 导出登录态 → 导入云端」。

支持两种输入格式
----------------
1. **Netscape cookies.txt**（最通用；Chrome 扩展 "Get cookies.txt LOCALLY"、
   EditThisCookie、curl --cookie-jar 等都能导出）
   每行 7 字段：domain  flag  path  secure  expires  name  value

2. **JSON**（Playwright storageState 或 [{name,value,domain,...}] 列表）

用法
----
    python scripts/import_login_state.py --input <cookies.txt|state.json> \
        --profile ~/.flux-chrome-profile

安全
----
- 本脚本**不打印任何 Cookie 值**，只输出条数与域名统计。
- 写入前自动备份原 Cookies 库。
- Cookie 仅落在运行工作目录，**不进技能包、不进交付物**。
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys
import time

# Chrome 时间戳：1601-01-01 起算的微秒
_CHROME_EPOCH_OFFSET = 11644473600  # 1601→1970 的秒差


def _to_chrome_time(unix_ts):
    return int((unix_ts + _CHROME_EPOCH_OFFSET) * 1_000_000)


def parse_netscape(path):
    """解析 Netscape cookies.txt。返回 (cookie_dict, 统计)。"""
    out = []
    skipped = 0
    with open(path, encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.rstrip('\n')
            if not line.strip():
                continue
            if line.startswith('#'):
                # 有些导出器把 cookie 写在 "#HttpOnly_" 前缀行里
                if line.startswith('#HttpOnly_'):
                    line = line[len('#HttpOnly_'):]
                else:
                    continue
            parts = line.split('\t')
            if len(parts) < 7:
                parts = line.split()
            if len(parts) < 7:
                skipped += 1
                continue
            domain, _flag, cpath, secure, expires, name, value = parts[:7]
            try:
                exp = int(float(expires))
            except ValueError:
                exp = 0
            out.append({
                'domain': domain.lstrip('.'),
                'host_key': domain if domain.startswith('.') else '.' + domain.lstrip('.'),
                'path': cpath or '/',
                'secure': 1 if str(secure).upper() in ('TRUE', '1') else 0,
                'expires': exp,
                'name': name,
                'value': value,
                'httponly': 1 if name.lower() in ('httponly',) else 0,
            })
    return out, {'skipped': skipped}


def parse_json(path):
    """解析 Playwright storageState 或 cookie 列表 JSON。"""
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    if isinstance(data, dict) and 'cookies' in data:
        raw = data['cookies']
    elif isinstance(data, list):
        raw = data
    else:
        raise ValueError('无法识别的 JSON 结构：既没有 cookies 字段，也不是列表')
    out = []
    for c in raw:
        dom = str(c.get('domain') or '').lstrip('.')
        out.append({
            'domain': dom,
            'host_key': c.get('domain') or ('.' + dom),
            'path': c.get('path') or '/',
            'secure': 1 if c.get('secure') else 0,
            'expires': int(c.get('expires') or c.get('expirationDate') or 0),
            'name': c.get('name', ''),
            'value': c.get('value', ''),
            'httponly': 1 if (c.get('httpOnly') or c.get('httponly')) else 0,
        })
    return out, {}


def load_cookies(path):
    low = path.lower()
    if low.endswith('.json'):
        return parse_json(path)
    return parse_netscape(path)


def resolve_db(profile):
    for cand in ('Default/Cookies', 'Cookies', 'Profile 1/Cookies'):
        p = os.path.join(os.path.expanduser(profile), cand)
        if os.path.exists(p):
            return p
    raise SystemExit('找不到 Cookies 数据库，profile 目录是否正确：%s' % profile)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', required=True, help='cookies.txt 或 state.json')
    ap.add_argument('--profile', default='~/.flux-chrome-profile')
    ap.add_argument('--dry-run', action='store_true', help='只统计不写入')
    args = ap.parse_args(argv)

    src = os.path.expanduser(args.input)
    if not os.path.exists(src):
        raise SystemExit('输入文件不存在：%s' % src)

    cookies, meta = load_cookies(src)
    if not cookies:
        raise SystemExit('未解析到任何 Cookie，请检查文件格式')

    # 按域名统计（只输出域名，不输出值）
    domains = {}
    for c in cookies:
        domains[c['domain']] = domains.get(c['domain'], 0) + 1

    print('解析到 Cookie：%d 条' % len(cookies))
    if meta.get('skipped'):
        print('  跳过无效行：%d' % meta['skipped'])
    print('涉及域名 %d 个：' % len(domains))
    for d, n in sorted(domains.items(), key=lambda kv: -kv[1])[:15]:
        print('  %-40s %d 条' % (d, n))

    target = resolve_db(args.profile)
    print('目标库：%s' % target)

    if args.dry_run:
        print('--dry-run，未写入')
        return 0

    # 备份
    stamp = time.strftime('%Y%m%d-%H%M%S')
    backup = '%s.bak-%s' % (target, stamp)
    shutil.copy2(target, backup)
    print('已备份原库：%s' % backup)

    con = sqlite3.connect(target)
    try:
        cols = [r[1] for r in con.execute('PRAGMA table_info(cookies)')]
        now = _to_chrome_time(time.time())
        inserted = updated = 0
        for c in cookies:
            exp = _to_chrome_time(c['expires']) if c['expires'] > 0 else 0
            has_exp = 1 if exp > 0 else 0
            is_persistent = has_exp
            row = {
                'creation_utc': now,
                'host_key': c['host_key'],
                'top_frame_site_key': '',
                'name': c['name'],
                'value': c['value'],
                'encrypted_value': b'',
                'path': c['path'],
                'expires_utc': exp,
                'is_secure': c['secure'],
                'is_httponly': c['httponly'],
                'last_access_utc': now,
                'has_expires': has_exp,
                'is_persistent': is_persistent,
                'priority': 1,
                'samesite': -1,
                'source_scheme': 1 if c['secure'] else 0,
                'source_port': 443 if c['secure'] else 80,
                'last_update_utc': now,
                'source_type': 0,
                'has_cross_site_ancestor': 0,
            }
            row = {k: v for k, v in row.items() if k in cols}
            keys = list(row.keys())
            placeholders = ','.join('?' * len(keys))
            collist = ','.join('"%s"' % k for k in keys)

            cur = con.execute(
                'SELECT count(*) FROM cookies WHERE host_key=? AND name=? AND path=?',
                (c['host_key'], c['name'], c['path']),
            )
            exists = cur.fetchone()[0]
            if exists:
                sets = ','.join('"%s"=?' % k for k in keys if k not in ('creation_utc',))
                vals = [row[k] for k in keys if k not in ('creation_utc',)]
                con.execute(
                    'UPDATE cookies SET %s WHERE host_key=? AND name=? AND path=?' % sets,
                    vals + [c['host_key'], c['name'], c['path']],
                )
                updated += 1
            else:
                con.execute('INSERT INTO cookies (%s) VALUES (%s)' % (collist, placeholders),
                            [row[k] for k in keys])
                inserted += 1
        con.commit()
        total = con.execute('SELECT count(*) FROM cookies').fetchone()[0]
    finally:
        con.close()

    print('写入完成：新增 %d，更新 %d，库内共 %d 条' % (inserted, updated, total))
    print('提示：需重启浏览器生效，且**不要在写入前打开浏览器**（否则会被覆盖回写）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
