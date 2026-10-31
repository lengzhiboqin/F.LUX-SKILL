# -*- coding: utf-8 -*-
"""门店推广（一站式推广）数据采集。

## 采集方式（已实测）

- 平台域：`waimaieapp.meituan.com`（闪购「门店推广」进入）
- **免 token**：同域 Cookie 有效即可（省略 token 直接成功；错误 token 会触发 logon）
- 广告账户 `acctId=226094983`：品牌级，全店通用
- 门店标识 `wmPoiId`：即门店主档 `shangou.merchant_ids[0]`
- 三个接口：
  - today/info      今日实时（按小时）
  - history?days=7  近7日（汇总 + 按天 details，含昨日）
  - history?days=30 近30日（汇总 + 按天 details）

## 输出

每店一份原始 JSON（API 原件），落到 `_data/ad_raw/<run_day>/wmPoiId_<id>.json`，
并写采集清单；后续由清洗/入库模块消费。

用法：
    python scripts/ad_collect.py --master _data/store_master.json --limit 3
    python scripts/ad_collect.py --master _data/store_master.json
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common.cdp import QN  # noqa: E402

AD_HOME = "https://waimaieapp.meituan.com/ad/v1/rpc"
AD_HOST = "waimaieapp.meituan.com"
ACCT_ID = "226094983"
PROD = "shangou_onestop"

JS_COLLECT_BODY = """
(async () => {
  const acct = arg.acct, poi = arg.poi;
  const get = async (u) => {
    try {
      const r = await fetch(u, {credentials:'include'});
      const t = await r.text();
      return {status:r.status, text:t};
    } catch(e) { return {error:String(e)}; }
  };
  const common = "/ad/v4/effect/multi/shangou_onestop/history?acctId=" + acct
    + "&wmPoiId=" + poi + "&platform=0&prodTypeName=" + "%s";
  return {
    today: await get("/ad/v3/statistics/shangou_onestop/today/info?acctId=" + acct
      + "&wmPoiId=" + poi + "&platform=0"),
    d7: await get(common + "&days=7"),
    d30: await get(common + "&days=30")
  };
})()
""" % PROD


def find_ad_session(qn):
    """找已有的广告平台标签并 attach（含已 detach 的）。"""
    for p in qn.call("Target.getTargets")["targetInfos"]:
        if p.get("type") == "page" and AD_HOST in p.get("url", ""):
            return qn._attach(p["targetId"])
    return None


def ensure_ad_tab(qn):
    s = find_ad_session(qn)
    if s:
        return s
    tid = qn.call("Target.createTarget", {"url": AD_HOME})["targetId"]
    s = qn._attach(tid)
    time.sleep(7)
    return s


def ad_ready(qn, s):
    """确认广告页面登录态有效（同域调一个轻量接口，非 logon 脚本）。"""
    v = qn.js("location.href", s, timeout=15)
    return isinstance(v, str) and AD_HOST in v


def load_stores(master_path):
    d = json.load(open(master_path, encoding="utf-8"))
    out = []
    for st in d["stores"]:
        ids = (st.get("shangou") or {}).get("merchant_ids") or []
        if not ids or st.get("status") != "active":
            continue
        out.append({
            "master_id": st["master_id"],
            "wmPoiId": str(ids[0]),
            "name": (st.get("qianniu") or {}).get("name", ""),
        })
    return out


def parse_ok(raw):
    """一次采集的三个接口是否都返回业务 JSON（code=0）。"""
    if not isinstance(raw, dict):
        return False, "no dict"
    for k in ("today", "d7", "d30"):
        piece = raw.get(k) or {}
        if piece.get("error"):
            return False, k + ":" + piece["error"]
        t = piece.get("text", "")
        if not t.strip().startswith("{"):
            return False, k + ":非JSON(可能logon)"
        try:
            if json.loads(t).get("code") != 0:
                return False, k + ":code!=0"
        except ValueError:
            return False, k + ":解析失败"
    return True, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--master", required=True)
    ap.add_argument("--out", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_data", "ad_raw"))
    ap.add_argument("--run-day", default=time.strftime("%Y-%m-%d"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.25)
    args = ap.parse_args()

    stores = load_stores(args.master)
    if args.limit:
        stores = stores[:args.limit]

    qn = QN(url=AD_HOME)               # 无 qnh 标签时会以广告 url 兜底
    s = ensure_ad_tab(qn)
    if not ad_ready(qn, s):
        raise SystemExit("广告平台登录态不可用，需重新登录闪购")

    out_dir = os.path.join(args.out, args.run_day)
    os.makedirs(out_dir, exist_ok=True)
    manifest = []
    n_ok = 0
    for i, st in enumerate(stores, 1):
        expression = "var arg = " + json.dumps(
            {"acct": ACCT_ID, "poi": st["wmPoiId"]}) + ";\n" + JS_COLLECT_BODY
        raw = qn.js(expression, s, timeout=60)
        ok, why = parse_ok(raw)
        rec = {"master_id": st["master_id"], "wmPoiId": st["wmPoiId"],
               "name": st["name"], "ok": ok, "note": why}
        if ok:
            p = os.path.join(out_dir, "wmPoiId_%s.json" % st["wmPoiId"])
            with open(p, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False)
            rec["file"] = p
            n_ok += 1
        manifest.append(rec)
        print("[%d/%d] %s %s %s" % (i, len(stores), "✓" if ok else "✗",
                                    st["wmPoiId"], "" if ok else why), flush=True)
        time.sleep(args.sleep)

    mp = os.path.join(out_dir, "采集清单.json")
    with open(mp, "w", encoding="utf-8") as f:
        json.dump({"run_day": args.run_day, "total": len(stores),
                   "ok": n_ok, "items": manifest}, f, ensure_ascii=False, indent=1)
    print("\n完成：成功 %d / %d ｜ 清单 %s" % (n_ok, len(stores), mp))
    qn.close()
    return 0 if n_ok == len(stores) else 1


if __name__ == "__main__":
    sys.exit(main())
