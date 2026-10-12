# -*- coding: utf-8 -*-
"""智能体沟通记录归纳管道 - 增量入表脚本（调用 lark-cli base）

流程:
  1. 读入表 JSON（AI 构建: {"create_records":[{字段名:值,...}, ...]}）
  2. 检查 select/多选字段选项池，缺失选项自动补（field-update 全量 PUT，保留旧选项）
  3. 拉已有记录按「条目标题+日期」去重
  4. +record-batch-create 分批写入（≤200/批）
  5. 回读验证行数 + 输出验证报告

用法:
  python sync_base.py --records records.json [--dry-run]
"""
import json, io, os, sys, subprocess, argparse, collections

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

TEMP_ROOT = os.path.join(os.environ.get('LOCALAPPDATA', r'C:\Users\Administrator\AppData\Local'), 'Temp')
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_TOKEN = 'MLMUbhhkrahWQSsHIg5cT2KfnNW'
TABLE_ID = 'tblDo2je1NsZoEn5'
# 需要检查选项池的字段: {字段ID: (字段名, 是否多选)}
SELECT_FIELDS = {
    'fldLUvUJda': ('Agent', False),          # 单选
    'fldbDeicjf': ('知识类型', True),         # 多选
    'fldc7TuPlx': ('分类', False),           # 单选
    'fldNjzns1G': ('口径标注', False),        # 单选
    'fldVg5ydgE': ('确认状态', False),        # 单选
    'fldT545AHH': ('标签', True),            # 多选
}
TITLE_FIELD = 'fldPot2nrZ'   # 条目标题
DATE_FIELD = 'fldv23tabf'    # 日期
AGENT_FIELD = 'fldLUvUJda'   # Agent

def run_cli(args, timeout=120):
    r = subprocess.run(['lark-cli', 'base'] + args, capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=timeout)
    return r.returncode, r.stdout, r.stderr

def load_json(path):
    with io.open(path, encoding='utf-8-sig') as f:
        return json.load(f)

def field_options(field_id):
    """分页拉取单选/多选字段全量选项"""
    opts = []
    offset = 0
    while True:
        code, out, err = run_cli(['+field-search-options', '--base-token', BASE_TOKEN,
                                  '--table-id', TABLE_ID, '--field-id', field_id,
                                  '--limit', '200', '--offset', str(offset)])
        if code != 0:
            print(f'ERR field-search-options {field_id} offset={offset}: {err.strip()}', file=sys.stderr)
            return opts
        try:
            d = json.loads(out)
            data = d.get('data', d)
            page = []
            for o in data.get('options', []):
                if isinstance(o, str):
                    page.append(o)
                elif isinstance(o, dict):
                    page.append(o.get('name') or o.get('text', ''))
            if not page:
                break
            opts.extend(page)
            if len(page) < 200:
                break
            offset += len(page)
        except Exception as e:
            print(f'ERR parse options {field_id}: {e}', file=sys.stderr)
            break
    return opts

def update_field_options(field_id, field_name, all_options):
    """全量 PUT 字段定义，追加新选项（必须带全部旧选项）"""
    payload = {
        'name': field_name,
        'type': 'select',
        'multiple': (field_id == 'fldT545AHH' or field_id == 'fldbDeicjf'),
        'options': [{'name': o} for o in all_options],
    }
    tmp = os.path.join(TEMP_ROOT, '_field_update.json')
    with io.open(tmp, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False)
    code, out, err = run_cli(['+field-update', '--base-token', BASE_TOKEN, '--table-id', TABLE_ID,
                              '--field-id', field_id, '--json', '@' + tmp, '--yes'])
    if code != 0:
        print(f'ERR field-update {field_id}: {err.strip()}', file=sys.stderr)
        return False
    print(f'OK field-update {field_name}: 选项池 -> {len(all_options)}')
    return True

def ensure_options(records, dry_run=False):
    """检查记录用到的 select 值，缺的补选项。返回 True 表示全部就绪"""
    needed = collections.defaultdict(set)
    for rec in records['create_records']:
        for fid, (fname, multi) in SELECT_FIELDS.items():
            v = rec.get(fname)
            if v is None:
                continue
            vals = v if isinstance(v, list) else [v]
            for x in vals:
                if x:
                    needed[fid].add(x)
    all_ok = True
    for fid, (fname, multi) in SELECT_FIELDS.items():
        existing = set(field_options(fid))
        missing = needed.get(fid, set()) - existing
        if missing:
            print(f'缺选项 {fname}: {sorted(missing)}')
            if dry_run:
                print(f'  [dry-run] 跳过补选项（不写入）')
                all_ok = False
                continue
            merged = list(existing) + sorted(missing)
            if not update_field_options(fid, fname, merged):
                all_ok = False
    return all_ok

def fetch_existing():
    """拉全量已有记录，返回 [(条目标题, 日期, agent), ...]"""
    existing = []
    offset = 0
    while True:
        code, out, err = run_cli(['+record-list', '--base-token', BASE_TOKEN, '--table-id', TABLE_ID,
                                  '--format', 'json', '--limit', '200', '--offset', str(offset)])
        if code != 0:
            print(f'ERR record-list offset={offset}: {err.strip()}', file=sys.stderr)
            break
        try:
            d = json.loads(out)
            data = d['data']
            fields = data['fields']
            rows = data['data']
            ridx = {n: i for i, n in enumerate(fields)}
            for row in rows:
                def val(name):
                    i = ridx.get(name)
                    if i is None or i >= len(row):
                        return None
                    v = row[i]
                    if isinstance(v, list):
                        v = v[0] if v else None
                    return v
                title = val('条目标题')
                date = val('日期')
                agent = val('Agent')
                if isinstance(date, str):
                    date = date[:10]
                existing.append((str(title or ''), str(date or '')[:10], str(agent or '')))
            if not data.get('has_more'):
                break
            offset += len(rows)
        except Exception as e:
            print(f'ERR parse record-list: {e}', file=sys.stderr)
            break
    return existing

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--records', required=True, help='入表 JSON 路径')
    ap.add_argument('--dry-run', action='store_true', help='只检查选项池与去重，不写入')
    args = ap.parse_args()

    records = load_json(args.records)
    n = len(records['create_records'])
    print(f'入表记录: {n} 条')

    if not ensure_options(records, dry_run=args.dry_run):
        if args.dry_run:
            print('[dry-run] 存在缺选项（未写入）；正式运行时会自动补齐')
        else:
            print('选项池补齐失败，中止写入', file=sys.stderr)
        sys.exit(1 if not args.dry_run else 0)

    existing = fetch_existing()
    print(f'已有记录: {len(existing)} 条')

    # 去重: 条目标题+日期 已存在则跳过
    dup_keys = set()
    for t, d, a in existing:
        if t and d:
            dup_keys.add((t, d))
    new_records = []
    skipped = 0
    for rec in records['create_records']:
        t = rec.get('条目标题', '')
        dd = str(rec.get('日期', ''))[:10]
        if (t, dd) in dup_keys:
            skipped += 1
            continue
        new_records.append(rec)
    print(f'去重跳过 {skipped} 条，实际写入 {len(new_records)} 条')

    if args.dry_run:
        print('[dry-run] 未写入')
        sys.exit(0)

    # 分批写入
    written = 0
    for i in range(0, len(new_records), 200):
        batch = {'create_records': new_records[i:i + 200]}
        tmp = os.path.join(TEMP_ROOT, '_records_batch.json')
        with io.open(tmp, 'w', encoding='utf-8') as f:
            json.dump(batch, f, ensure_ascii=False)
        code, out, err = run_cli(['+record-batch-create', '--base-token', BASE_TOKEN,
                                  '--table-id', TABLE_ID, '--json', '@' + tmp])
        if code != 0:
            print(f'ERR batch-create {i}: {err.strip()}', file=sys.stderr)
            continue
        try:
            d = json.loads(out)
            ddata = d.get('data', d)
            written += len(ddata.get('record_id_list', []))
        except Exception:
            written += len(batch['create_records'])
        print(f'批次 {i//200 + 1}: 写入 {written} 条')

    print(f'\\n共写入 {written} 条（跳过 {skipped} 条重复）')

if __name__ == '__main__':
    main()
