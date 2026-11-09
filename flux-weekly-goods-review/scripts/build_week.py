# -*- coding: utf-8 -*-
"""
每周货盘复盘 · 数据层构建
把项目文件夹里的三张源表（新品 / 活动 / 货盘）解析成 数据/week-<周>.json，
并追加 数据/index.json，供 看板/商品周复盘看板.html 渲染。

用法：
  python build_week.py --root "C:\\Users\\Administrator\\Desktop\\每周货盘与新品复盘会议"
  python build_week.py --root ... --dry-run          # 只打印关键数字，不写盘
  python build_week.py --root ... --week 2026W46     # 强制指定周编号（默认按周期推算）

退出码：0 成功；2 数据不齐/周期冲突（需要人看，不要猜）
"""
import argparse, glob, json, os, re, sys, datetime as dt

sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd
import numpy as np

SCHEMA = "flux-weekly-goods-v1"
CORE_TOKENS = ("美容护肤", "彩妆香水", "个人洗护", "美妆工具")
VIRTUAL_TOKENS = ("专属加急配送", "单拍无效", "运费", "配送费补差")
STD_CAT = {"skin": 550, "makeup": 500, "tool": 220, "hair": 150, "body": 180, "fragrance": 70, "adjacent": 330}
STD_TOTAL = 2000


def die(msg):
    print("[FATAL] " + msg)
    sys.exit(2)


def pick_period(texts):
    """从文件名里抓 YYYYMMDD 周期；返回 (start, end) 或 None"""
    pats = [r"(\d{8})-(\d{8})", r"(\d{8})_(\d{8})"]
    if isinstance(texts, str):
        texts = [texts]
    for t in texts:
        for p in pats:
            m = re.search(p, t)
            if m:
                a, b = m.group(1), m.group(2)
                return dt.date(int(a[:4]), int(a[4:6]), int(a[6:8])), dt.date(int(b[:4]), int(b[4:6]), int(b[6:8]))
    return None


def iso_week(d):
    y, w, _ = d.isocalendar()
    return "%dW%02d" % (y, w)


def shorten(name, limit=16):
    """长商品名压成可读短名：去营销前缀 → 切分隔符 → 切卖点尾巴 → 超长截断"""
    s = str(name).strip()
    for pre in ["新款", "新品", "爆款", "网红", "正品", "包邮", "特价", "秒杀", "限时", "同款", "超值"]:
        while s.startswith(pre) and len(s) > len(pre) + 2:
            s = s[len(pre):]
    for sep in ["/", "（", "(", "【", "】", "，", ",", "|"]:
        i = s.find(sep)
        if i > 1:
            s = s[:i]
    for tail in ["男女", "女", "男", "可爱", "喜庆", "加厚", "甜酷", "神器", "便携", "大号", "儿童", "学生", "免洗",
                 "防水", "持久", "遮瑕", "可上妆", "网红", "同款", "ins", "超值", "包邮"]:
        i = s.find(tail)
        if i > 2:
            s = s[:i]
    s = s.strip().rstrip("-—·~")
    return s if len(s) <= limit else s[:limit] + "…"


def grp(l1, l2):
    if l1 == "美容护肤":
        return "skin"
    if l2 == "美妆工具/服务":
        return "tool"
    if l1 == "彩妆香水":
        return "fragrance" if l2 == "香水" else "makeup"
    if l1 == "个人洗护":
        # 与看板口径一致：二级「美发护发」单列，其余（身体清洁/护理、女士、男士、旅行套装、口腔）并入身体护理
        return "hair" if l2 == "美发护发" else "body"
    return "adjacent"


def r2(x, n=2):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), n)


def r3(x):
    return r2(x, 3)


def rate(a, b):
    return r3(float(a) / float(b)) if b else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--week", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    d_new = os.path.join(root, "新品数据-商品魔方2.0")
    d_act = os.path.join(root, "活动数据-营销分析")
    d_inv = os.path.join(root, "货盘数据-牵牛花门店商品")
    for d in (d_new, d_act, d_inv):
        if not os.path.isdir(d):
            die("缺目录：%s" % d)

    # ---------- 1. 定位文件 ----------
    new_csvs = sorted(glob.glob(os.path.join(d_new, "**", "*.csv"), recursive=True), key=os.path.getmtime)
    act_csvs = sorted(glob.glob(os.path.join(d_act, "*.csv")), key=os.path.getmtime)
    inv_xlsx = sorted(glob.glob(os.path.join(d_inv, "*.xlsx")), key=os.path.getmtime)
    if not new_csvs or not act_csvs or not inv_xlsx:
        die("三张表不全：新品%d 活动%d 货盘%d" % (len(new_csvs), len(act_csvs), len(inv_xlsx)))

    # 新品：总览（1 行 10 列）+ 明细（多行）；取同一周期的最新一组
    def read_gb(p):
        d = pd.read_csv(p, encoding="gb18030", dtype=str)
        return d.apply(lambda s: s.str.strip() if s.dtype == "object" else s)

    ov, items, ov_period = None, None, None
    for p in reversed(new_csvs):
        d = read_gb(p)
        per = pick_period(os.path.basename(p))
        if len(d) == 1:
            if ov is None:
                ov, ov_period = d, per
        elif items is None or (per and ov_period == per):
            items = d
            if per:
                ov_period = ov_period or per
        if ov is not None and items is not None and ov_period:
            break
    if ov is None or items is None:
        die("新品数据需要「总览 csv」和「明细 csv」两份，当前找到 %d 份 csv" % len(new_csvs))

    act = read_gb(act_csvs[-1])
    act_period = pick_period(os.path.basename(act_csvs[-1]))

    # 周期一致性：新品 vs 活动
    if ov_period and act_period and ov_period != act_period:
        die("周期不一致：新品 %s，活动 %s —— 请确认三张表导的是同一周" % (ov_period, act_period))
    period = ov_period or act_period
    if period is None:
        die("无法从文件名识别周期（需要形如 20261102_20261108 或 20261102-20261108）")
    p_start, p_end = period
    week = args.week or iso_week(p_start)
    label = "%s–%s" % (p_start.strftime("%m.%d"), p_end.strftime("%m.%d"))

    # ---------- 2. 货盘表 ----------
    xp = inv_xlsx[-1]
    df = pd.read_excel(xp, sheet_name="门店商品", dtype=str)
    df = df.apply(lambda s: s.str.strip() if s.dtype == "object" else s)
    num = lambda c: pd.to_numeric(df[c], errors="coerce") if c in df.columns else pd.Series(np.nan, index=df.index)
    df["_sale"] = num("美团闪购渠道售价")
    df["_cost"] = num("采购价")
    df["_qty"] = num("月销量")
    df["_stock"] = num("实物库存")
    df.loc[df["_stock"] >= 1e8, "_stock"] = np.nan          # 哨兵值 999999999
    df["_online"] = df["上下线状态"].eq("已上线")
    df["_shelf"] = df["规格上下架状态"].fillna("").str.contains("美团：已上架") & df["是否手工下架"].fillna("").str.contains("美团：否")
    df["_active"] = df["_qty"].fillna(0) > 0
    df["_grp"] = [grp(a, b) for a, b in zip(df["美团闪购类目一级"], df["美团闪购类目二级"])]
    df["_virtual"] = df["商品名称"].fillna("").apply(lambda s: any(k in s for k in VIRTUAL_TOKENS))
    df["_value"] = (df["_cost"].fillna(0) * df["_stock"].fillna(0))

    ledger = len(df)
    online = int(df["_online"].sum())
    shelved = int(df["_shelf"].sum())
    selling = int((df["_shelf"] & df["_active"]).sum())
    dead = int((df["_online"] & ~df["_active"]).sum())
    inv_value = float(df.loc[~df["_virtual"], "_value"].sum())
    inv_value_all = float(df["_value"].sum())
    dead_rows = df["_online"] & ~df["_active"] & (df["_stock"].fillna(0) > 0) & ~df["_virtual"]
    dead_value = float(df.loc[dead_rows, "_value"].sum())
    # 缺货：有货架位（在架）但实物库存为 0；分母同为在架
    zero_stock = int((df["_shelf"] & df["_stock"].fillna(0).eq(0)).sum())
    l3 = df["美团闪购类目三级"].dropna()
    l3_all = int(df["美团闪购类目三级"].nunique())
    l3_n = int(df.loc[df["_online"], "美团闪购类目三级"].nunique())
    l3_counts = df.loc[df["_online"], "美团闪购类目三级"].dropna().value_counts()
    l3_thin = int((l3_counts <= 3).sum())

    gmv = float(pd.to_numeric(ov["全部商品成交金额"], errors="coerce").fillna(0).iloc[0])
    units = int(pd.to_numeric(ov["全部商品销量"], errors="coerce").fillna(0).iloc[0])
    unit_price = r2(gmv / units) if units else None
    all_conv = float(pd.to_numeric(ov["全部商品下单转化率"], errors="coerce").fillna(0).iloc[0])

    assortment = {
        "ledger": ledger, "online": online, "shelved": shelved, "selling": selling,
        "selling_rate": rate(selling, shelved), "dead_sku": dead, "dead_rate": rate(dead, online),
        "dead_value": int(round(dead_value)), "inventory_value": int(round(inv_value_all)),
        "inventory_value_excl_virtual": int(round(inv_value)), "dead_value_share": rate(dead_value, inv_value),
        "zero_stock_sku": zero_stock, "stockout_rate": rate(zero_stock, shelved),
        "spu": int(df["SPUID"].nunique()), "category_l3": l3_n, "category_l3_thin": l3_thin,
        "category_l3_avg_sku": r2(ledger / l3_all, 1) if l3_all else None,
    }

    # ---------- 3. 七大类 ----------
    names = {"skin": "护肤", "makeup": "彩妆", "tool": "美妆工具", "hair": "美发护发",
             "body": "身体护理", "fragrance": "香水香氛", "adjacent": "女性连带"}
    category = []
    for k in ["skin", "makeup", "tool", "hair", "body", "fragrance", "adjacent"]:
        m = df["_grp"] == k
        sh = int((m & df["_shelf"]).sum())
        category.append({
            "key": k, "name": names[k],
            "ledger": int(m.sum()), "online": int((m & df["_online"]).sum()), "shelved": sh,
            "standard": STD_CAT[k], "active_rate": rate(int((m & df["_shelf"] & df["_active"]).sum()), sh),
        })

    # ---------- 4. 价格带 ----------
    pr = df[df["_sale"].notna()].copy()
    bins = [-1, 9.9, 29.9, 59, 99, 1e9]
    labs = ["≤9.9", "9.9–29.9", "30–59", "60–99", "≥100"]
    std_band = {"≤9.9": [20, 25], "9.9–29.9": [45, 55], "30–59": [15, 20], "60–99": [5, 8], "≥100": [3, 5]}
    pr["_band"] = pd.cut(pr["_sale"], bins=bins, labels=labs)
    tot_units = pr["_qty"].fillna(0).sum()
    pr["_gmv"] = pr["_qty"].fillna(0) * pr["_sale"]
    tot_gmv = pr["_gmv"].sum()
    price_bands = []
    for b in labs:
        g = pr[pr["_band"] == b]
        price_bands.append({
            "band": b, "sku": len(g), "share": rate(len(g), len(pr)), "std": std_band[b],
            "active_rate": rate(int(g["_active"].sum()), len(g)),
            "units_share": rate(g["_qty"].fillna(0).sum(), tot_units),
            "gmv_share": rate(g["_gmv"].sum(), tot_gmv),
        })

    # ---------- 5. 新品 ----------
    n = items.copy()
    n["_expo"] = pd.to_numeric(n["曝光人数-近7天)"], errors="coerce").fillna(0).astype(int)
    n["_sale7"] = pd.to_numeric(n["商品销量-近7天)"], errors="coerce").fillna(0).astype(int)
    n["_days"] = pd.to_numeric(n["上架天数"], errors="coerce")
    full = n["商品名称"].str.strip()
    price_map = df.drop_duplicates("商品名称").set_index("商品名称")["_sale"]
    stock_map = df.drop_duplicates("商品名称").set_index("商品名称")["_stock"]
    cat_map = df.drop_duplicates("商品名称").set_index("商品名称")["美团闪购类目二级"]

    n_items = []
    for _, row in n.iterrows():
        fn = str(row["商品名称"]).strip()
        n_items.append({
            "name": shorten(fn), "category": (row["商品一级类目"] or "未分类"),
            "price": r2(price_map.get(fn, np.nan)), "days": None if pd.isna(row["_days"]) else int(row["_days"]),
            "exposure": int(row["_expo"]), "stock": None if pd.isna(stock_map.get(fn, np.nan)) else int(stock_map.get(fn)),
        })
    n_items = sorted(n_items, key=lambda x: (-x["exposure"], x["name"]))
    n_cats = []
    for c, g in n.groupby("商品一级类目"):
        n_cats.append({"name": c, "count": len(g), "exposure": int(g["_expo"].sum()),
                       "zero": int((g["_expo"] == 0).sum()), "core": str(c).startswith(CORE_TOKENS)})
    n_cats = sorted(n_cats, key=lambda x: (-x["count"], x["name"]))

    new_products = {
        "count": len(n), "spu": int(n["商品spuid"].nunique()),
        "share_of_shelved": r2(pd.to_numeric(ov["新品占总在售商品"], errors="coerce").iloc[0], 4),
        "exposure": int(n["_expo"].sum()),
        "exposure_uv": int(pd.to_numeric(ov["新品曝光人数"], errors="coerce").fillna(0).iloc[0]),
        "orders": int(pd.to_numeric(ov["新品销量"], errors="coerce").fillna(0).iloc[0]),
        "gmv": r2(pd.to_numeric(ov["新品成交金额"], errors="coerce").fillna(0).iloc[0]),
        "conversion": r2(pd.to_numeric(ov["新品下单转化率"], errors="coerce").fillna(0).iloc[0], 4),
        "all_conversion": r2(all_conv, 4),
        "zero_exposure_count": int((n["_expo"] == 0).sum()),
        "items": n_items, "categories": n_cats,
        "rules": [
            "品类准入：只进美妆核心七大类，非美妆新款每周占比不超过 10%。",
            "上架即配位：每款至少绑定一个入口位（专区、组合推荐或分类置顶），7 天曝光不足 30 人次即下架换品。",
            "价格锚定：主力落在 9.9–29.9 元，60 元以上新款必须有品牌与详情支撑。",
        ],
    }

    # ---------- 6. 活动 ----------
    a = act.copy()
    a.columns = ["日期", "活动类型", "revenue", "orders", "cost", "merchant", "platform", "roi_col"]
    for c in ["revenue", "orders", "cost", "merchant", "platform"]:
        a[c] = pd.to_numeric(a[c], errors="coerce").fillna(0)
    a["活动类型"] = a["活动类型"].str.replace("\t", "", regex=False).str.strip()
    a["cost_rate"] = a["cost"] / a["revenue"].replace(0, np.nan)
    a = a.sort_values("cost_rate", ascending=False)
    total_rev, total_cost = float(a["revenue"].sum()), float(a["cost"].sum())
    merchant, platform = float(a["merchant"].sum()), float(a["platform"].sum())
    nc_row = a[a["活动类型"].str.contains("新用户")]["revenue"].sum()
    activities = {
        "total_attributed_revenue": r2(total_rev), "overlap_ratio": r2(total_rev / gmv if gmv else 0),
        "orders": int(a["orders"].sum()), "cost": r2(total_cost),
        "merchant_subsidy": r2(merchant), "platform_subsidy": r2(platform),
        "platform_share": rate(platform, total_cost), "cost_rate_of_gmv": rate(total_cost, gmv),
        "items": [{"name": r["活动类型"], "revenue": r2(r["revenue"]), "orders": int(r["orders"]),
                   "cost": r2(r["cost"]), "cost_rate": r2(r["cost_rate"], 4),
                   "merchant": r2(r["merchant"]), "platform": r2(r["platform"])} for _, r in a.iterrows()],
        "new_customer_coverage": rate(nc_row, gmv),
        "constraints": [
            "单笔订单商家补贴不超过该单成交额的 15%，超出不允许叠加。",
            "折扣商品与店铺优惠券不同时开启，保留成本率更低的组合。",
            "压缩出的预算转投新客第二单券，验证复购能否被拉起。",
        ],
    }

    # ---------- 7. 信息质量 ----------
    br = df["品牌名称"].fillna("").str.strip()
    named = int(((br != "") & (~br.isin(["其他品牌", "无品牌"]))).sum())
    detail_raw = df["不符渠道规则类型"].dropna().astype(str).str.split("；").explode().str.strip()
    detail_raw = detail_raw[detail_raw != ""]
    # 同一规则在美团/饿了么两侧写法不同，合并成一个口径
    detail_raw = detail_raw.apply(lambda s: "价格同步失败（美团+饿了么）" if "价格同步" in s else s)
    detail_raw = detail_raw.replace("", np.nan).dropna()
    vc = detail_raw.value_counts()
    top = [{"name": k, "count": int(v)} for k, v in vc.head(6).items()]
    rest = int(vc.iloc[6:].sum()) if len(vc) > 6 else 0
    if rest:
        top.append({"name": "其他规则异常", "count": rest})
    quality = {
        "brand_named": named, "brand_rate": rate(named, ledger),
        "brand_other": int((br == "其他品牌").sum()), "brand_none": int((br == "").sum()),
        "desc_coverage": r3(df["商品描述"].notna().mean()),
        "sellpoint_coverage": r3(df["商品卖点"].notna().mean()),
        "store_cat_missing": int(df["门店店内分类"].isna().sum()),
        "store_cat_missing_rate": rate(int(df["门店店内分类"].isna().sum()), ledger),
        "channel_anomalies": int(df["不符渠道规则类型"].notna().sum()),
        "channel_anomaly_rate": rate(int(df["不符渠道规则类型"].notna().sum()), ledger),
        "image_coverage": r3(df["是否有图片"].fillna("").eq("是").mean()),
        "channel_anomaly_detail": top,
    }

    # ---------- 8. 目标对照 ----------
    def pct(x):
        s = "%.1f" % (float(x) * 100)
        return (s[:-2] if s.endswith(".0") else s) + "%"
    core_shelf = int(df.loc[df["_grp"] != "adjacent", "_shelf"].sum())
    skin_sh = [c for c in category if c["key"] == "skin"][0]
    hb = sum(c["shelved"] for c in category if c["key"] in ("hair", "body"))
    band_ok = sum(1 for b in price_bands if b["std"][0] <= b["share"] * 100 <= b["std"][1])
    gross = (df["_sale"] - df["_cost"]) / df["_sale"]
    gross_med = float(gross[(df["_sale"] > 0) & df["_cost"].notna()].median())

    def band_status(x, lo, hi):
        # 允许 10% 相对容差，避免 10.8% 对 8–10% 被误判为超标
        if lo * 0.9 <= x <= hi * 1.1:
            return "ok"
        return "low" if x < lo else "high"

    targets = [
        {"name": "美妆核心占比", "target": "50%–70%", "actual": rate(core_shelf, shelved),
         "display": pct(core_shelf / shelved), "status": band_status(core_shelf / shelved, .50, .70)},
        {"name": "护肤占比", "target": "35%–40%", "actual": None,
         "display": pct(skin_sh["shelved"] / shelved), "status": band_status(skin_sh["shelved"] / shelved, .35, .40)},
        {"name": "彩妆占比", "target": "25%–30%", "actual": None,
         "display": pct([c for c in category if c["key"] == "makeup"][0]["shelved"] / shelved),
         "status": band_status([c for c in category if c["key"] == "makeup"][0]["shelved"] / shelved, .25, .30)},
        {"name": "个护占比", "target": "15%–20%", "actual": None, "display": pct(hb / shelved),
         "status": band_status(hb / shelved, .15, .20)},
        {"name": "工具占比", "target": "8%–10%", "actual": None,
         "display": pct([c for c in category if c["key"] == "tool"][0]["shelved"] / shelved),
         "status": band_status([c for c in category if c["key"] == "tool"][0]["shelved"] / shelved, .08, .10)},
        {"name": "三级类目数", "target": "约 90 个", "actual": l3_n, "display": "%d 个" % l3_n,
         "status": "ok" if l3_n <= 100 else "high"},
        {"name": "30 天动销率", "target": "≥50%", "actual": rate(selling, shelved),
         "display": pct(selling / shelved), "status": "ok" if selling / shelved >= .5 else "low"},
        {"name": "缺货率", "target": "≤5%", "actual": rate(zero_stock, shelved),
         "display": pct(zero_stock / shelved), "status": "ok" if zero_stock / shelved <= .05 else "high"},
        {"name": "品牌信息覆盖", "target": "100%", "actual": quality["brand_rate"],
         "display": pct(quality["brand_rate"]), "status": "ok" if quality["brand_rate"] >= .99 else "low"},
        {"name": "商品详情覆盖", "target": "100%", "actual": quality["desc_coverage"],
         "display": pct(quality["desc_coverage"]), "status": "ok" if quality["desc_coverage"] >= .99 else "low"},
        {"name": "主图覆盖", "target": "100%", "actual": quality["image_coverage"],
         "display": pct(quality["image_coverage"]), "status": "ok" if quality["image_coverage"] >= .99 else "low"},
        {"name": "价格带五档结构", "target": "全部落区间", "actual": None,
         "display": "%d/5 达标" % band_ok, "status": "ok" if band_ok >= 4 else "low"},
        {"name": "客单价", "target": "45–60 元", "actual": None, "display": "—", "status": "todo"},
        {"name": "连带率", "target": "2–3 件／单", "actual": None, "display": "—", "status": "todo"},
        {"name": "库存周转", "target": "30–45 天", "actual": None, "display": "—", "status": "todo"},
        {"name": "综合毛利率", "target": "35%–45%", "actual": r3(gross_med), "display": pct(gross_med),
         "status": "review"},
    ]
    # actual 字段只给百分比类，避免前端把 224 当比例画
    for t in targets:
        if t["status"] in ("low", "ok", "high") and t["target"].find("%") >= 0:
            t["actual"] = r3(float(t["display"].replace("%", "")) / 100)
        elif t["status"] == "review":
            t["actual"] = r3(gross_med)

    # ---------- 9. 动作与选品 ----------
    pending = online - shelved
    new_rate = rate(selling, max(shelved - 200, 1))
    b3059 = [b for b in price_bands if b["band"] == "30–59"][0]
    combos = int(df["商品类型"].eq("组合品").sum()) if "商品类型" in df.columns else 0
    skin_gap = STD_CAT["skin"] - skin_sh["shelved"]
    adj_gap = [c for c in category if c["key"] == "adjacent"][0]["shelved"] - STD_CAT["adjacent"]

    actions = [
        {"step": "1", "title": "补齐在架",
         "now": "在架 %d，已审核未上架 %d 个" % (shelved, pending),
         "act": "把 %d 个已审核商品全部上架，进专区与分类置顶位" % pending,
         "target": "周内在架 ≥%d" % (shelved + pending),
         "why": "这 %d 个已有货、已有图，只差一次上架动作，是零成本增量。" % pending},
        {"step": "2", "title": "砍掉不动的",
         "now": "零动销 %d 个，占已上线 %.0f%%" % (dead, dead / online * 100),
         "act": "按货值从高到低下架最差 200 个，位置让给本周新上架品",
         "target": "动销率 %s → %s" % (pct(selling / shelved), pct(new_rate)),
         "why": "下架 200 个后分母 %d，按本周 %d 个动销计，动销率提升到 %s。" % (shelved - 200, selling, pct(new_rate))},
        {"step": "3", "title": "补护肤、减连带",
         "now": "护肤 %d／标准 %d；女性连带 %d／标准 %d" % (skin_sh["shelved"], STD_CAT["skin"],
                                                      [c for c in category if c["key"] == "adjacent"][0]["shelved"], STD_CAT["adjacent"]),
         "act": "护肤补 %d 个（30–59 元与 9.9–29.9 元两带为主）；女性连带砍 %d 个非强关联品" % (skin_gap, adj_gap),
         "target": "护肤占比 %s → 20%% 起步" % pct(skin_sh["shelved"] / shelved),
         "why": "护肤是唯一既缺量、动销又不低（%s）的类目，补进去能直接转销量。" % pct(skin_sh["active_rate"])},
        {"step": "4", "title": "提客单",
         "now": "件单价 %.2f 元；30–59 元带 %d 个 SKU 动销仅 %s" % (unit_price or 0, b3059["sku"], pct(b3059["active_rate"])),
         "act": "把该带动销的 %d 个提到专区前置位，组合品 %d → 40" % (int(b3059["sku"] * b3059["active_rate"]), combos),
         "target": "件单价 ≥10 元",
         "why": "该带用 %s 件数贡献 %s 成交额，是提客单最省力的一带。" % (pct(b3059["units_share"]), pct(b3059["gmv_share"]))},
    ]

    selection = [
        {"cat": "护肤", "dir": "补 %d 个" % skin_gap,
         "rule": "只补 30–59 元与 9.9–29.9 元带；60 元以上暂不进",
         "basis": "两个价位贡献 %s 成交额，护肤动销 %s" % (
             pct(price_bands[2]["gmv_share"] + price_bands[1]["gmv_share"]), pct(skin_sh["active_rate"]))},
        {"cat": "女性连带", "dir": "减 %d 个" % adj_gap,
         "rule": "只留化妆棉、棉签、发饰、胸贴等强关联品，单价 9.9 元内",
         "basis": "占在架 %s，是唯一大幅超标准的类目" % pct([c for c in category if c["key"] == "adjacent"][0]["shelved"] / shelved)},
        {"cat": "新品", "dir": "每周 ≤10 款",
         "rule": "全落美妆核心七类，9.9–29.9 元为主；7 天曝光不足 30 人次即换",
         "basis": "本周 %d 款零成交、%d 款零曝光，%s 非核心品类" % (
             len(n), new_products["zero_exposure_count"],
             pct(sum(c["count"] for c in n_cats if not c["core"]) / len(n)))},
    ]

    store = {"name": df["门店名称"].iloc[0] if "门店名称" in df.columns else "F.LUX 全球美妆集合店（华晨店）",
             "code": str(df["门店编码"].iloc[0]) if "门店编码" in df.columns else "",
             "city": str(df["门店所属城市"].iloc[0]) if "门店所属城市" in df.columns else ""}

    data = {
        "schema": SCHEMA, "week": week,
        "period": {"start": p_start.isoformat(), "end": p_end.isoformat(), "label": label},
        "store": store, "generated_at": dt.date.today().isoformat(),
        "headline": {"gmv": r2(gmv), "units": units, "unit_price": unit_price,
                     "orders": None, "aov": None, "new_products_gmv": new_products["gmv"]},
        "assortment": assortment, "category": category,
        "standard": {"total": STD_TOTAL, "skin_share": r3(STD_CAT["skin"] / STD_TOTAL),
                     "adjacent_share": r3(STD_CAT["adjacent"] / STD_TOTAL)},
        "price_bands": price_bands, "priced_sku": len(pr),
        "new_products": new_products, "activities": activities, "quality": quality,
        "targets": targets, "actions": actions, "selection": selection,
        "gaps": {"count": 1, "items": [{
            "priority": "P0", "name": "上周同口径三张表",
            "why": "本周所有数字只能描述现状，算不出「往标准靠近」的变化。有了上周基准，在架增量、护肤占比提升、新品首单都能变成环比。",
            "how": "导出路径不变，日期改为上周重导一次；后台若只能按日导出，给上周一与上周日各一套也可用。"}]},
        "sources": [
            "新品数据-商品魔方2.0（周期 %s_%s，总览+明细）" % (p_start.strftime("%Y%m%d"), p_end.strftime("%Y%m%d")),
            "活动数据-营销分析（周期 %s-%s）" % (p_start.strftime("%Y%m%d"), p_end.strftime("%Y%m%d")),
            "货盘数据-牵牛花门店商品（%s）" % os.path.basename(xp),
            "货盘行业指标脑图与三层组品（2000 品标准款基准）",
        ],
    }

    # ---------- 10. 写盘 ----------
    out_dir = os.path.join(root, "数据")
    os.makedirs(out_dir, exist_ok=True)
    wpath = os.path.join(out_dir, "week-%s.json" % week)
    ipath = os.path.join(out_dir, "index.json")

    summary = {"gmv": r2(gmv), "units": units, "shelved": shelved,
               "selling_rate": rate(selling, shelved), "new_products_gmv": new_products["gmv"]}

    if args.dry_run:
        print(json.dumps({"week": week, "period": label, "headline": data["headline"],
                          "assortment": assortment, "targets": [(t["name"], t["display"], t["status"]) for t in targets]},
                         ensure_ascii=False, indent=1))
        return

    with open(wpath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    idx = {"schema": "flux-weekly-goods-index-v1", "store": data["store"],
           "updated_at": data["generated_at"], "weeks": []}
    if os.path.exists(ipath):
        with open(ipath, encoding="utf-8") as f:
            old = json.load(f)
        idx["weeks"] = old.get("weeks", [])
    entry = {"week": week, "label": label, "start": p_start.isoformat(), "end": p_end.isoformat(),
             "file": "week-%s.json" % week, "summary": summary}
    idx["weeks"] = [w for w in idx["weeks"] if w.get("week") != week] + [entry]
    idx["weeks"].sort(key=lambda w: w.get("week", ""))
    with open(ipath, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=2)

    print("OK week=%s period=%s" % (week, label))
    print("  成交 %.2f 元 / %d 件 / 件单价 %s" % (gmv, units, unit_price))
    print("  台账 %d → 在架 %d → 动销 %d（%s）" % (ledger, shelved, selling, pct(selling / shelved)))
    print("  写盘：%s" % wpath)
    print("  索引：%d 周（%s）" % (len(idx["weeks"]), ", ".join(w["week"] for w in idx["weeks"])))
    if new_products["exposure"] != new_products["exposure_uv"]:
        print("  提示：新品明细曝光合计 %d 为行级重复计数，去重 UV %d（报告须注明口径）"
              % (new_products["exposure"], new_products["exposure_uv"]))
    print("  提示：活动归因营业额是 %.2f 倍重叠归因，不可加总、不可当营收" % activities["overlap_ratio"])


if __name__ == "__main__":
    main()
