# -*- coding: utf-8 -*-
"""智能体沟通记录归纳管道 - 统一提取脚本
从 Codex / Claude / WorkBuddy / Hermes 的本地会话文件中提取用户消息，按天分组输出。

用法:
  python extract_agents.py --agent codex [--date 2026-10-01]
  python extract_agents.py --agent claude
  python extract_agents.py --agent workbuddy
  python extract_agents.py --agent hermes
  python extract_agents.py --all

输出: 每段以 "===== <agent> <date> (<N>条) =====" 开头，随后每行 "[HH:MM:SS] <text>"
"""
import json, glob, os, sys, re, io, argparse
from datetime import datetime, timezone, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
TZ = timezone(timedelta(hours=8))

# ---------- 通用清洗 ----------
def clean_text(t):
    t = t.replace('<environment_context>', '').replace('</environment_context>', '')
    for tag in ['image', 'filesystem', 'permission_profile', 'current_date', 'timezone',
                'workspace_roots', 'file_system', 'entry', 'root', 'special', 'path',
                'access', 'truncated', 'guardian_truncated', 'ide_opened_file',
                'local-command-caveat', 'local-command-stdout', 'command-name',
                'command-message', 'command-args', 'task-notification', 'recommended_plugins',
                'cwd', 'shell', 'subagents']:
        t = re.sub(r'<' + tag + r'[^>]*>.*?</' + tag + r'>', '', t, flags=re.S)
    return t.strip()

def is_system_msg(text):
    t = text.strip()
    if not t:
        return True
    markers = [
        '# AGENTS.md instructions', '<INSTRUCTIONS>', 'The following is the Codex agent history',
        '>>> RETAINED USER INSTRUCTIONS', '>>> TRUSTED USER ANSWERS', '>>> TRANSCRIPT DELTA',
        '<external_codex_apps_open_page>', '<hook_prompt', '<permissions instructions>',
        '</permissions instructions>', 'This is a request to approve', 'You are being asked to approve',
        'Please confirm that you want to run', 'The user would like you to', 'We need your permission',
        'Press Enter to continue', 'Distinguish instructions in attached documents',
        '## My request:', '# Files mentioned by the user:',
        'You are judging one planned coding-agent action', 'Assess the exact action',
        'Base Risk Taxonomy', 'Outcome Policy', '# Security Policy', '# Evidence Handling',
        'You may use read-only tool checks', 'For low-risk actions, give the final answer directly',
    ]
    for m in markers:
        if m in t or t.startswith(m):
            return True
    return False

def get_ts(ts):
    try:
        dt = datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
        return dt.astimezone(TZ)
    except Exception:
        return None

def msg_text(payload):
    content = payload.get('content', [])
    texts = []
    if isinstance(content, list):
        for c in content:
            if isinstance(c, dict):
                if c.get('type') in ('input_text', 'text'):
                    texts.append(c.get('text', ''))
    else:
        texts.append(str(content))
    return '\n'.join(texts)

def add_msg(by_day, dt, text, src):
    if dt is None or not text or len(text) < 2:
        return
    ds = dt.strftime('%Y-%m-%d')
    by_day.setdefault(ds, []).append({'ts': dt.strftime('%H:%M:%S'), 'text': text[:800], 'src': src})

# ---------- 各 Agent 提取 ----------
def extract_codex(by_day, date_filter):
    base = r'C:\Users\Administrator\.codex\sessions'
    files = glob.glob(os.path.join(base, '2026', '*', '*', '*', 'rollout-*.jsonl')) or \
            glob.glob(os.path.join(base, '2026', '*', '*', 'rollout-*.jsonl'))
    files.sort()
    for f in files:
        try:
            with open(f, encoding='utf-8') as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if obj.get('type') == 'response_item':
                        p = obj.get('payload', {})
                        if p.get('type') == 'message' and p.get('role') == 'user':
                            raw = clean_text(msg_text(p))
                            if raw and not is_system_msg(raw):
                                dt = get_ts(p.get('timestamp', obj.get('timestamp', '')))
                                add_msg(by_day, dt, raw, f)
                    elif obj.get('type') == 'event_msg':
                        p = obj.get('payload', {})
                        if p.get('type') == 'user_message':
                            raw = clean_text(str(p.get('content', '')))
                            if raw and not is_system_msg(raw):
                                add_msg(by_day, get_ts(obj.get('timestamp', '')), raw, f)
        except Exception as e:
            print(f'ERR {f}: {e}', file=sys.stderr)

def extract_claude(by_day, date_filter):
    base = r'C:\Users\Administrator\.claude\projects'
    files = glob.glob(os.path.join(base, '**', '*.jsonl'), recursive=True)
    files.sort()
    for f in files:
        try:
            with open(f, encoding='utf-8') as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if obj.get('type') == 'user':
                        c = obj.get('message', {})
                        content = c.get('content', '')
                        if isinstance(content, list):
                            parts = []
                            for item in content:
                                if isinstance(item, dict) and item.get('type') in ('text', 'input_text'):
                                    parts.append(item.get('text', ''))
                            raw = clean_text('\n'.join(parts))
                        else:
                            raw = clean_text(str(content))
                        if raw and not is_system_msg(raw):
                            add_msg(by_day, get_ts(obj.get('timestamp', '')), raw, f)
        except Exception as e:
            print(f'ERR {f}: {e}', file=sys.stderr)

def extract_workbuddy(by_day, date_filter):
    base = r'C:\Users\Administrator\.workbuddy\projects'
    files = glob.glob(os.path.join(base, '**', '*.jsonl'), recursive=True)
    files.sort()
    for f in files:
        try:
            with open(f, encoding='utf-8') as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    # 真实用户消息
                    if obj.get('role') == 'user' and isinstance(obj.get('content'), str):
                        raw = clean_text(obj['content'])
                        if raw and not is_system_msg(raw):
                            add_msg(by_day, get_ts(obj.get('timestamp', '')), raw, f)
                    # summary 块：包含 "All user messages" 列表或 summary 文本
                    t = obj.get('type', '')
                    if 'summary' in str(t).lower():
                        txt = json.dumps(obj, ensure_ascii=False)[:2000]
                        if 'user message' in txt.lower():
                            dt = get_ts(obj.get('timestamp', obj.get('created_at', '')))
                            add_msg(by_day, dt, '[summary] ' + txt[:1500], f)
        except Exception as e:
            print(f'ERR {f}: {e}', file=sys.stderr)

def extract_hermes(by_day, date_filter):
    base = r'C:\Users\Administrator\.hermes'
    if not os.path.isdir(base):
        print('Hermes 目录不存在，跳过: ' + base, file=sys.stderr)
        return
    files = glob.glob(os.path.join(base, '**', '*.jsonl'), recursive=True)
    files.sort()
    for f in files:
        try:
            with open(f, encoding='utf-8') as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if obj.get('role') == 'user' and isinstance(obj.get('content'), str):
                        raw = clean_text(obj['content'])
                        if raw and not is_system_msg(raw):
                            add_msg(by_day, get_ts(obj.get('timestamp', '')), raw, f)
        except Exception as e:
            print(f'ERR {f}: {e}', file=sys.stderr)

EXTRACTORS = {
    'codex': extract_codex,
    'claude': extract_claude,
    'workbuddy': extract_workbuddy,
    'hermes': extract_hermes,
}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--agent', choices=list(EXTRACTORS.keys()) + ['all'], default='all')
    ap.add_argument('--date', default=None, help='YYYY-MM-DD 过滤，缺省全部')
    args = ap.parse_args()

    by_day = {}
    agents = list(EXTRACTORS.keys()) if args.agent == 'all' else [args.agent]
    for a in agents:
        sub = {}
        EXTRACTORS[a](sub, args.date)
        for ds, msgs in sub.items():
            if args.date and ds != args.date:
                continue
            by_day.setdefault(ds, []).extend([{**m, 'agent': a} for m in msgs])

    for ds in sorted(by_day.keys()):
        print(f'\n===== {ds} ({len(by_day[ds])}条) =====')
        for m in sorted(by_day[ds], key=lambda x: x['ts']):
            print(f'[{m["ts"]}] ({m["agent"]}) {m["text"]}')

if __name__ == '__main__':
    main()
