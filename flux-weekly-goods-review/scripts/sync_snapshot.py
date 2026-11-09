# -*- coding: utf-8 -*-
"""
把 数据/week-<周>.json 写回 HTML 的 <script id="embedded-data">，
让双击打开（file:// 读不到同目录 JSON）时仍能渲染当周数据。

用法：
  python sync_snapshot.py --root "...\\每周货盘与新品复盘会议"
  python sync_snapshot.py --root ... --week 2026W46
  python sync_snapshot.py --root ... --check-only    # 只校验一致性，不写

退出码：0 一致/已写入；1 校验失败；2 文件缺失
"""
import argparse, json, os, re, sys

sys.stdout.reconfigure(encoding="utf-8")

EMB_RE = re.compile(r'(<script id="embedded-data" type="application/json">)([\s\S]*?)(</script>)')


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--week", default=None)
    ap.add_argument("--check-only", action="store_true")
    a = ap.parse_args()

    root = os.path.abspath(a.root)
    html_p = os.path.join(root, "看板", "商品周复盘看板.html")
    idx_p = os.path.join(root, "数据", "index.json")
    if not os.path.exists(html_p):
        print("[FATAL] 缺看板：%s" % html_p); sys.exit(2)
    if not os.path.exists(idx_p):
        print("[FATAL] 缺索引：%s（先跑 build_week.py）" % idx_p); sys.exit(2)

    idx = load(idx_p)
    weeks = idx.get("weeks", [])
    if not weeks:
        print("[FATAL] 索引里没有周数据"); sys.exit(2)
    week = a.week or weeks[-1]["week"]
    jp = os.path.join(root, "数据", "week-%s.json" % week)
    if not os.path.exists(jp):
        print("[FATAL] 缺数据文件：%s" % jp); sys.exit(2)
    data = load(jp)

    html = open(html_p, encoding="utf-8").read()
    m = EMB_RE.search(html)
    if not m:
        print("[FATAL] 看板里找不到 embedded-data 块"); sys.exit(1)

    emb_text = m.group(2)
    try:
        emb = json.loads(emb_text)
        emb_week = emb.get("week")
    except Exception as e:
        emb, emb_week = None, None
        print("[WARN] 现有内嵌快照不是合法 JSON：%s" % e)

    same = emb is not None and json.dumps(emb, sort_keys=True, ensure_ascii=False) == \
        json.dumps(data, sort_keys=True, ensure_ascii=False)

    if a.check_only:
        print("embedded week=%s  external week=%s  equal=%s" % (emb_week, week, same))
        sys.exit(0 if same else 1)

    payload = json.dumps(data, ensure_ascii=False, indent=2)
    if "</script" in payload:
        print("[FATAL] 数据里含 </script，无法安全内嵌"); sys.exit(1)
    new_html = html[:m.start(2)] + "\n" + payload + "\n" + html[m.end(2):]
    open(html_p, "w", encoding="utf-8").write(new_html)

    # 回读校验：内嵌==外部、div 配平、体积
    back = open(html_p, encoding="utf-8").read()
    emb2 = json.loads(EMB_RE.search(back).group(2))
    ok_json = json.dumps(emb2, sort_keys=True, ensure_ascii=False) == json.dumps(data, sort_keys=True, ensure_ascii=False)
    opens = len(re.findall(r"<div\b", back))
    closes = len(re.findall(r"</div>", back))
    size = len(back.encode("utf-8"))

    print("写入周：%s（%s）" % (week, data.get("period", {}).get("label")))
    print("  内嵌==外部：%s" % ok_json)
    print("  div 配平：open %d / close %d" % (opens, closes))
    print("  HTML 体积：%d 字节" % size)
    if not ok_json or opens != closes:
        print("[FAIL] 校验未通过"); sys.exit(1)
    print("OK 快照已同步")


if __name__ == "__main__":
    main()
