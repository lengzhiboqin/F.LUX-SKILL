#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F.LUX每日经营简报生成器（v2）

数据源：F.LUX品牌数据汇总表.xlsx（8张sheet：门店财务明细/门店成交明细/商品数据/
问题订单数据/流量明细(新)/流量渠道明细(新)/评价数据/售后订单数据）

用法：
  python3 generate_briefing.py --excel <path.xlsx> --date 20260915 [--out <dir>]

说明：
  - --date 必填，简报数据日期（格式 YYYYMMDD 或 YYYY-MM-DD）
  - 输出复杂版：F.LUX每日经营简报_YYYYMMDD_v2_full.md（11章节全维度）
  - 净收入口径：收入-佣金-配送服务费-商家补贴金额-公益捐款-其他费用（与原03简报一致）
"""
import argparse, json, os, re, sys
from datetime import datetime, timedelta
from collections import Counter, defaultdict


def to_std_date(v):
    """日期显式转换：兼容 20260913 / 2026-09-13 / 2026-09-13 00:00:00 / 20260801-20260831 区间"""
    s = str(v).strip()
    m = re.search(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})", s)
    if m:
        return "%04d%02d%02d" % (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m2 = re.search(r"\d{8}", s)
    if m2:
        return m2.group(0)
    return s


def num(v):
    """安全转float，失败返回None"""
    try:
        if v is None:
            return None
        s = str(v).strip()
        if s in ("", "--", "nan", "None"):
            return None
        return float(s)
    except (ValueError, TypeError):
        return None


def clean_rows(rows, date_col, target_date):
    """清洗并筛选目标日期行"""
    return [r for r in rows if to_std_date(r.get(date_col, "")) == target_date]


def read_sheet(excel_path, sheet):
    """读取Excel sheet为dict列表（openpyxl）"""
    import openpyxl
    wb = openpyxl.load_workbook(excel_path, read_only=True)
    ws = wb[sheet]
    rows_iter = ws.iter_rows(values_only=True)
    headers = [str(h).lstrip("\ufeff").strip() if h else "" for h in next(rows_iter)]
    out = []
    for row in rows_iter:
        if not any(c is not None and str(c).strip() for c in row):
            continue
        out.append({headers[i]: (row[i] if i < len(row) else None) for i in range(len(headers))})
    wb.close()
    return out


def fmt_money(v):
    if v is None:
        return "--"
    return "¥{:,.2f}".format(v)


def fmt_pct(v):
    """环比变化：带+号"""
    if v is None:
        return "--"
    return "{:+.1f}%".format(v * 100)


def fmt_ratio(v):
    """比率/占比：不带+号"""
    if v is None:
        return "--"
    return "{:.1f}%".format(v * 100)


def pct_change(cur, prev):
    if cur is None or prev in (None, 0):
        return None
    return (cur - prev) / prev


def short_name(name):
    """门店名缩短：去掉品牌前缀和括号"""
    s = str(name)
    s = s.replace("F.LUX国际美妆集合店（", "").replace("F.LUX全球美妆集合店（", "")
    s = s.replace("F.LUX芙乐美妆初颜馆（", "").replace("（", "").replace("）", "").replace("(", "").replace(")", "")
    return s


# ==================== 数据统计 ====================

def analyze(excel_path, target_date):
    """读取Excel并计算全维度统计"""
    target = to_std_date(target_date)
    prev_date = ""
    try:
        dt = datetime.strptime(target, "%Y%m%d")
        prev_date = (dt - timedelta(days=1)).strftime("%Y%m%d")
    except ValueError:
        pass

    result = {"date": target, "prev_date": prev_date}

    # ---- 门店财务明细 ----
    fin = read_sheet(excel_path, "门店财务明细")
    fin_cur = clean_rows(fin, "开始时间", target)
    fin_prev = clean_rows(fin, "开始时间", prev_date) if prev_date else []
    result["fin_cur"] = fin_cur
    result["fin_prev"] = fin_prev

    # 净收入口径：收入-佣金-配送费-补贴-公益-其他（与原03简报一致）
    def net_income(r):
        return (num(r.get("收入")) or 0) - (num(r.get("佣金")) or 0) - (num(r.get("配送服务费")) or 0) \
            - (num(r.get("商家补贴金额")) or 0) - (num(r.get("公益捐款")) or 0) - (num(r.get("其他费用")) or 0)

    def agg_fin(rows):
        return {
            "营业额": sum(num(r.get("营业额")) or 0 for r in rows),
            "实付交易额": sum(num(r.get("实付交易额")) or 0 for r in rows),
            "净收入": sum(net_income(r) for r in rows),
            "有效订单": sum(num(r.get("有效订单数")) or 0 for r in rows),
            "佣金": sum(num(r.get("佣金")) or 0 for r in rows),
            "已取消订单": sum(num(r.get("已取消订单数")) or 0 for r in rows),
            "取消损失": sum(num(r.get("已取消订单损失金额")) or 0 for r in rows),
            "活跃门店": len({r.get("商家ID") for r in rows}),
        }

    cur_agg = agg_fin(fin_cur)
    prev_agg = agg_fin(fin_prev)
    result["cur_agg"] = cur_agg
    result["prev_agg"] = prev_agg
    result["客单价"] = (cur_agg["实付交易额"] / cur_agg["有效订单"]) if cur_agg.get("有效订单") else None

    # 门店排名
    store_agg = defaultdict(lambda: {"营业额": 0, "订单": 0, "净收入": 0})
    for r in fin_cur:
        sid = r.get("商家ID")
        store_agg[sid]["名称"] = r.get("商家名称", "")
        store_agg[sid]["营业额"] += num(r.get("营业额")) or 0
        store_agg[sid]["订单"] += num(r.get("有效订单数")) or 0
        store_agg[sid]["净收入"] += net_income(r)
    store_list = []
    for sid, v in store_agg.items():
        store_list.append({
            "id": sid, "名称": v["名称"], "营业额": v["营业额"],
            "订单": v["订单"], "净收入": v["净收入"],
            "客单价": (v["营业额"] / v["订单"]) if v["订单"] else 0,
            "净利率": (v["净收入"] / v["营业额"]) if v["营业额"] else 0,
        })
    store_list.sort(key=lambda x: -x["营业额"])
    result["store_top_revenue"] = store_list[:10]
    store_by_net = sorted(store_list, key=lambda x: -x["净收入"])
    result["store_top_net"] = store_by_net[:10]

    # 取消率
    total_orders = cur_agg["有效订单"] + cur_agg["已取消订单"]
    result["取消率"] = (cur_agg["已取消订单"] / total_orders) if total_orders else 0
    store_cancel = []
    for r in fin_cur:
        eff = num(r.get("有效订单数")) or 0
        cancel = num(r.get("已取消订单数")) or 0
        if eff + cancel >= 10:
            store_cancel.append({
                "名称": r.get("商家名称", ""),
                "取消率": cancel / (eff + cancel),
                "取消/总单": f"{int(cancel)}/{int(eff+cancel)}",
            })
    store_cancel.sort(key=lambda x: -x["取消率"])
    result["store_cancel_top"] = store_cancel[:5]

    # ---- 门店成交明细（服务质量）----
    deal = read_sheet(excel_path, "门店成交明细")
    deal_cur = clean_rows(deal, "开始日期", target)
    result["deal_cur"] = deal_cur

    # ---- 商品数据 ----
    prod = read_sheet(excel_path, "商品数据")
    prod_cur = clean_rows(prod, "日期", target)
    prod_prev = clean_rows(prod, "日期", prev_date) if prev_date else []
    result["prod_cur"] = prod_cur
    result["prod_prev"] = prod_prev
    prod_agg = defaultdict(lambda: {"qty": 0, "amount": 0, "分类": ""})
    for r in prod_cur:
        name = r.get("商品名称", "")
        prod_agg[name]["qty"] += num(r.get("商品销售数量")) or 0
        prod_agg[name]["amount"] += num(r.get("商品实付销售额")) or 0
        prod_agg[name]["分类"] = r.get("商品分类", "") or ""
    prod_list = [{"名称": k, **v} for k, v in prod_agg.items()]
    prod_list.sort(key=lambda x: -x["amount"])
    result["prod_top"] = prod_list[:10]
    result["prod_amount"] = sum(v["amount"] for v in prod_list)

    # ---- 流量明细 ----
    flow = read_sheet(excel_path, "流量明细(新)")
    flow_cur = clean_rows(flow, "日期", target)
    flow_prev = clean_rows(flow, "日期", prev_date) if prev_date else []
    result["flow_cur"] = flow_cur
    result["flow_prev"] = flow_prev

    def agg_flow(rows):
        return {
            "曝光": sum(num(r.get("曝光人数")) or 0 for r in rows),
            "入店": sum(num(r.get("入店人数")) or 0 for r in rows),
            "加购": sum(num(r.get("加购人数")) or 0 for r in rows),
            "下单": sum(num(r.get("下单人数")) or 0 for r in rows),
            "新客曝光": sum(num(r.get("曝光人数-品牌新客")) or 0 for r in rows),
            "新客下单": sum(num(r.get("下单人数-品牌新客")) or 0 for r in rows),
            "门店数": len({r.get("门店ID") for r in rows}),
        }

    result["flow_agg_cur"] = agg_flow(flow_cur)
    result["flow_agg_prev"] = agg_flow(flow_prev)

    # ---- 流量渠道 ----
    ch = read_sheet(excel_path, "流量渠道明细(新)")
    ch_cur = clean_rows(ch, "日期", target)
    ch_agg = defaultdict(lambda: {"曝光": 0, "入店": 0, "下单": 0})
    for r in ch_cur:
        chan = r.get("渠道", "")
        ch_agg[chan]["曝光"] += num(r.get("曝光人数")) or 0
        ch_agg[chan]["入店"] += num(r.get("入店人数")) or 0
        ch_agg[chan]["下单"] += num(r.get("下单人数")) or 0
    ch_list = [{"渠道": k, **v} for k, v in ch_agg.items()]
    ch_list.sort(key=lambda x: -x["曝光"])
    result["channel_top"] = ch_list

    # ---- 评价数据 ----
    eva = read_sheet(excel_path, "评价数据")
    eva_cur = clean_rows(eva, "评价提交日期", target)
    eva_prev = clean_rows(eva, "评价提交日期", prev_date) if prev_date else []
    result["eva_cur"] = eva_cur
    result["eva_prev"] = eva_prev
    eva_valid = [s for s in (num(r.get("商家评分")) for r in eva_cur) if s is not None]
    result["eva_avg"] = (sum(eva_valid) / len(eva_valid)) if eva_valid else None
    result["eva_count"] = len(eva_cur)
    result["eva_bad"] = [r for r in eva_cur if (num(r.get("商家评分")) or 5) <= 2]

    # ---- 售后订单 ----
    aft = read_sheet(excel_path, "售后订单数据")
    aft_cur = clean_rows(aft, "下单时间", target)
    aft_prev = clean_rows(aft, "下单时间", prev_date) if prev_date else []
    result["aft_cur"] = aft_cur
    result["aft_prev"] = aft_prev
    aft_types = Counter(str(r.get("售后类型", "")) for r in aft_cur)
    result["aft_types"] = dict(aft_types.most_common(5))
    result["aft_amount"] = sum(num(r.get("成功退款金额（元）")) or 0 for r in aft_cur)

    # ---- 问题订单 ----
    prob = read_sheet(excel_path, "问题订单数据")
    prob_cur = clean_rows(prob, "下单时间", target)
    result["prob_cur"] = prob_cur
    result["prob_count"] = len(prob_cur)

    # 总门店数（成交明细去重）
    result["total_stores"] = len({r.get("商家ID") for r in deal})

    return result


# ==================== 简报生成 ====================

def gen_full(d):
    """复杂版简报"""
    cur, prev = d["cur_agg"], d["prev_agg"]
    L = []
    L.append("# F.LUX品牌每日经营简报（v2 复杂版）")
    L.append(f"> **报告日期**：{d['date'][:4]}-{d['date'][4:6]}-{d['date'][6:]}（昨日数据）")
    L.append(f"> **生成时间**：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    L.append("> **数据来源**：F.LUX品牌数据汇总表.xlsx（技能产出，8表）")
    L.append("> **报告类型**：每日经营简报（P0 · 复杂版）")
    L.append("")
    L.append("---")
    L.append("")

    # 一、品牌整体经营
    L.append("## 一、品牌整体经营数据")
    L.append("")
    L.append("| 指标 | 数值 | 说明 |")
    L.append("|---|---|---|")
    L.append(f"| 营业额 | {fmt_money(cur['营业额'])} | 商品原价口径 |")
    L.append(f"| 实付交易额 | {fmt_money(cur['实付交易额'])} | 用户实际支付 |")
    L.append(f"| 净收入 | {fmt_money(cur['净收入'])} | 收入-佣金-配送-补贴等 |")
    L.append(f"| 有效订单数 | {int(cur['有效订单'])}单 | 已完成有效订单 |")
    L.append(f"| 实付单均价 | {fmt_money(d['客单价'])} | 实付/有效订单 |")
    L.append(f"| 佣金支出 | {fmt_money(cur['佣金'])} | 平台佣金 |")
    L.append(f"| 已取消订单 | {int(cur['已取消订单'])}单 | 损失{fmt_money(cur['取消损失'])} |")
    L.append(f"| 活跃门店 | {int(cur['活跃门店'])}/{d['total_stores']}家 | 财务有交易/总门店 |")
    L.append("")

    # 二、环比
    L.append("## 二、环比变化（昨日 vs 前日）")
    L.append("")
    L.append("| 指标 | 昨日 | 前日 | 环比 |")
    L.append("|---|---|---|---|")
    for k, label in [("营业额", "营业额"), ("实付交易额", "实付交易额"), ("净收入", "净收入"), ("有效订单", "有效订单数")]:
        c = cur.get(k); p = prev.get(k)
        if k == "有效订单":
            L.append(f"| {label} | {int(c)}单 | {int(p)}单 | {fmt_pct(pct_change(c, p))} |")
        else:
            L.append(f"| {label} | {fmt_money(c)} | {fmt_money(p)} | {fmt_pct(pct_change(c, p))} |")
    L.append("")
    L.append("**关键发现**：")
    L.append(f"- 营业额{fmt_pct(pct_change(cur['营业额'], prev['营业额']))}，实付{fmt_pct(pct_change(cur['实付交易额'], prev['实付交易额']))}，净收入{fmt_pct(pct_change(cur['净收入'], prev['净收入']))}，订单{fmt_pct(pct_change(cur['有效订单'], prev['有效订单']))}")
    L.append("")

    # 三、门店营业额TOP10
    L.append("## 三、门店营业额排名 TOP10")
    L.append("")
    L.append("| 排名 | 门店名称 | 营业额 | 订单数 | 客单价 |")
    L.append("|---|---|---|---|---|")
    for i, s in enumerate(d["store_top_revenue"], 1):
        L.append(f"| {i} | {short_name(s['名称'])} | {fmt_money(s['营业额'])} | {int(s['订单'])} | {fmt_money(s['客单价'])} |")
    top3 = sum(s["营业额"] for s in d["store_top_revenue"][:3])
    total = cur["营业额"]
    top3_names = "、".join(short_name(s["名称"]) for s in d["store_top_revenue"][:3])
    L.append("")
    L.append(f"**TOP3贡献占比**：{fmt_ratio(top3/total) if total else '--'}（{top3_names}）")
    L.append("")

    # 四、门店净收入TOP10
    L.append("## 四、门店净收入排名 TOP10")
    L.append("")
    L.append("| 排名 | 门店名称 | 净收入 | 营业额 | 净利率 |")
    L.append("|---|---|---|---|---|")
    for i, s in enumerate(d["store_top_net"], 1):
        L.append(f"| {i} | {short_name(s['名称'])} | {fmt_money(s['净收入'])} | {fmt_money(s['营业额'])} | {fmt_ratio(s['净利率'])} |")
    L.append("")

    # 五、商品维度
    L.append("## 五、商品销售 TOP10")
    L.append("")
    if d["prod_top"]:
        L.append("| 排名 | 商品名称 | 分类 | 销量 | 实付销售额 |")
        L.append("|---|---|---|---|---|")
        for i, p in enumerate(d["prod_top"][:10], 1):
            name = p["名称"] if len(p["名称"]) <= 22 else p["名称"][:22] + "…"
            cat = p["分类"].split("||")[-1] if p["分类"] else "--"
            L.append(f"| {i} | {name} | {cat} | {int(p['qty'])} | {fmt_money(p['amount'])} |")
        L.append("")
        L.append(f"**TOP10商品实付销售额**：{fmt_money(d['prod_amount'])}（占品牌实付 {fmt_ratio(d['prod_amount']/cur['实付交易额']) if cur['实付交易额'] else '--'}）")
    else:
        L.append("（当日无商品成交明细）")
    L.append("")

    # 六、流量维度
    fc, fp = d["flow_agg_cur"], d["flow_agg_prev"]
    L.append("## 六、流量分析")
    L.append("")
    L.append("| 指标 | 昨日 | 前日 | 环比 |")
    L.append("|---|---|---|---|")
    for k, label in [("曝光", "曝光人数"), ("入店", "入店人数"), ("加购", "加购人数"), ("下单", "下单人数"), ("新客曝光", "品牌新客曝光"), ("新客下单", "品牌新客下单")]:
        c, p = fc.get(k, 0), fp.get(k, 0)
        L.append(f"| {label} | {int(c)} | {int(p)} | {fmt_pct(pct_change(c, p))} |")
    L.append("")
    L.append(f"**漏斗转化**：曝光→入店 {fmt_ratio(fc['入店']/fc['曝光']) if fc['曝光'] else '--'}，入店→下单 {fmt_ratio(fc['下单']/fc['入店']) if fc['入店'] else '--'}")
    L.append("")

    # 七、渠道维度
    L.append("## 七、流量渠道分布")
    L.append("")
    if d["channel_top"]:
        L.append("| 渠道 | 曝光人数 | 入店人数 | 下单人数 | 入店率 |")
        L.append("|---|---|---|---|---|")
        for ch in d["channel_top"]:
            L.append(f"| {ch['渠道']} | {int(ch['曝光'])} | {int(ch['入店'])} | {int(ch['下单'])} | {fmt_ratio(ch['入店']/ch['曝光']) if ch['曝光'] else '--'} |")
    else:
        L.append("（当日无渠道数据）")
    L.append("")

    # 八、评价与售后
    L.append("## 八、评价与售后")
    L.append("")
    if d["eva_avg"]:
        L.append(f"- **评价**：昨日评价 {d['eva_count']} 条，平均评分 {d['eva_avg']:.2f}（满分5）")
    else:
        L.append(f"- **评价**：昨日评价 {d['eva_count']} 条，无评分数据")
    if d["eva_bad"]:
        bad_names = "、".join(short_name(r.get("店铺名称", "")) for r in d["eva_bad"][:3])
        L.append(f"- **差评门店**：{bad_names}（{len(d['eva_bad'])}家评分≤2）")
    L.append(f"- **售后**：昨日售后 {len(d['aft_cur'])} 笔，退款金额 {fmt_money(d['aft_amount'])}")
    if d["aft_types"]:
        L.append(f"- **售后类型TOP**：{'、'.join(f'{k}×{v}' for k, v in d['aft_types'].items())}")
    L.append(f"- **问题订单**：昨日 {d['prob_count']} 笔（退款/投诉问题）")
    L.append("")

    # 九、异常门店
    L.append("## 九、异常门店识别")
    L.append("")
    L.append("### 9.1 零订单门店")
    zero = [r for r in d["fin_cur"] if (num(r.get("有效订单数")) or 0) == 0]
    if zero:
        L.append("| 门店名称 | 状态 | 建议 |")
        L.append("|---|---|---|")
        for r in zero[:8]:
            L.append(f"| {short_name(r.get('商家名称',''))} | 有效订单为0 | 排查是否暂停营业/流量中断 |")
    else:
        L.append("（无零订单门店）")
    L.append("")
    L.append("### 9.2 高取消率门店（≥10单）TOP5")
    if d["store_cancel_top"]:
        L.append("| 排名 | 门店名称 | 取消率 | 取消/总单 |")
        L.append("|---|---|---|---|")
        for i, s in enumerate(d["store_cancel_top"], 1):
            L.append(f"| {i} | {short_name(s['名称'])} | {fmt_ratio(s['取消率'])} | {s['取消/总单']} |")
    else:
        L.append("（无高取消门店）")
    L.append("")
    L.append(f"**品牌整体取消率**：{fmt_ratio(d['取消率'])}（健康阈值<5%）")
    L.append("")

    # 十、关键发现与建议
    L.append("## 十、关键发现与运营建议")
    L.append("")
    L.append("### 10.1 亮点")
    highlights = []
    if cur["营业额"] and prev["营业额"] and cur["营业额"] > prev["营业额"]:
        highlights.append(f"营业额环比{fmt_pct(pct_change(cur['营业额'], prev['营业额']))}，经营向好")
    if d["store_top_net"] and d["store_top_net"][0]["净利率"] > 0.5:
        top = d["store_top_net"][0]
        highlights.append(f"{short_name(top['名称'])}净利率{fmt_ratio(top['净利率'])}，高盈利标杆")
    fc2 = d["flow_agg_cur"]
    if fc2["曝光"] > 0 and fc2["入店"] > 0 and fc2["入店"] / fc2["曝光"] > 0.05:
        highlights.append(f"曝光→入店率{fmt_ratio(fc2['入店']/fc2['曝光'])}，引流效率良好")
    if not highlights:
        highlights.append("整体经营平稳")
    for h in highlights[:3]:
        L.append(f"- {h}")
    L.append("")
    L.append("### 10.2 风险点")
    risks = []
    if d["取消率"] > 0.05:
        risks.append(f"整体取消率{fmt_ratio(d['取消率'])}，超过5%警戒线")
    if d["store_cancel_top"] and d["store_cancel_top"][0]["取消率"] > 0.15:
        risks.append(f"{short_name(d['store_cancel_top'][0]['名称'])}取消率{fmt_ratio(d['store_cancel_top'][0]['取消率'])}偏高")
    if top3 and total and top3 / total > 0.5:
        risks.append("TOP3门店贡献超50%，收入集中度高")
    if d["eva_bad"]:
        risks.append(f"{len(d['eva_bad'])}家门店差评（评分≤2）")
    if not risks:
        risks.append("暂无重大风险")
    for r_ in risks[:3]:
        L.append(f"- {r_}")
    L.append("")
    L.append("### 10.3 行动建议")
    L.append("**短期（本周）**：")
    L.append("1. 排查高取消率门店根因（缺货/拒单/配送），针对性优化")
    L.append("2. 确认零订单门店运营状态，检查曝光与转化")
    L.append("3. 复盘高净利率门店的选品与定价策略，向腰部门店推广")
    L.append("**中期（本月）**：")
    L.append("1. 将高净利率门店运营打法标准化（选品结构、定价、补贴）")
    L.append("2. 关注收入集中度风险，制定尾部门店提升计划")
    L.append("3. 建立取消率/差评周监控机制")
    L.append("")

    # 十一、辅助资料声明
    L.append("## 十一、辅助资料使用声明")
    L.append(f"- **数据源**：F.LUX品牌数据汇总表.xlsx（8表），数据日期{d['date'][:4]}-{d['date'][4:6]}-{d['date'][6:]}")
    L.append(f"- **对比基准**：前一日（{d['prev_date'][:4]}-{d['prev_date'][4:6]}-{d['prev_date'][6:]}）")
    L.append("- **实事求是声明**：所有数据均直接来自技能产出总表，未编造")
    L.append("")
    L.append("---")
    L.append("*本报告由F.LUX简报技能（v2）自动生成*")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--excel", required=True, help="Excel总表路径")
    ap.add_argument("--date", required=True, help="数据日期 YYYYMMDD")
    ap.add_argument("--out", default=".", help="输出目录")
    args = ap.parse_args()

    d = analyze(args.excel, args.date)
    os.makedirs(args.out, exist_ok=True)

    fname = f"F.LUX每日经营简报_{d['date']}_v2_full.md"
    out_path = os.path.join(args.out, fname)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(gen_full(d))

    # 写统一格式的last_run.json（供validator检测运行状态）
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        last_run_file = os.path.join(script_dir, "..", "data", "last_run.json")
        os.makedirs(os.path.dirname(last_run_file), exist_ok=True)
        last_run = {
            "ok": True,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "data_date": d["date"],
            "exit_code": 0,
            "error": None,
            "outputs": [out_path],
        }
        with open(last_run_file, "w", encoding="utf-8") as f:
            json.dump(last_run, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 写last_run.json失败: {e}", file=sys.stderr)
    print(json.dumps({
        "date": d["date"], "prev_date": d["prev_date"],
        "cur_agg": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in d["cur_agg"].items()},
        "prev_agg": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in d["prev_agg"].items()},
        "客单价": round(d["客单价"], 2) if d["客单价"] else None,
        "取消率": round(d["取消率"], 4),
        "flow_cur": {k: int(v) for k, v in d["flow_agg_cur"].items()},
        "channel_top": d["channel_top"][:3],
        "eva_count": d["eva_count"], "eva_avg": d["eva_avg"],
        "eva_bad_cnt": len(d["eva_bad"]),
        "aft_count": len(d["aft_cur"]), "aft_amount": round(d["aft_amount"], 2),
        "prob_count": d["prob_count"],
        "store_top_revenue": [{"名称": s["名称"], "营业额": round(s["营业额"], 2),
                               "订单": int(s["订单"]), "客单价": round(s["客单价"], 2)} for s in d["store_top_revenue"][:3]],
        "store_top_net": [{"名称": s["名称"], "净收入": round(s["净收入"], 2),
                           "净利率": round(s["净利率"], 4)} for s in d["store_top_net"][:2]],
        "store_cancel_top": [{"名称": s["名称"], "取消率": round(s["取消率"], 4)} for s in d["store_cancel_top"][:2]],
        "zero_store_names": [r.get("商家名称", "") for r in d["fin_cur"] if (num(r.get("有效订单数")) or 0) == 0][:8],
        "total_stores": d["total_stores"],
        "files": [fname],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
