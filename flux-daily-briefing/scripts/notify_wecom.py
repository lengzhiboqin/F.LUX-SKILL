#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F.LUX每日经营简报 · 企业微信通知（v4 - 对齐用户标准范式）

格式说明（重要）：
  企微 markdown 中单换行（\n）会粘连成一行，必须用**空行**（\n\n）分隔每条。
  时间用"X点"不用"X:XX"（冒号会被企微解析器吃掉）。
  结构：标题 → 📊核心数据 → 🏆TOP3 → ⚠️异常与风险 → 💡今日行动 → 📄报告位置 → ⏰下一任务。

用法：
  python3 notify_wecom.py --summary <简报JSON> [--date 20260915]
  python3 notify_wecom.py --send "自定义消息文本"
"""
import argparse, json, os, subprocess, sys
from datetime import datetime

CHAT_ID = "woeC4LQgAAk8Ci2f7Xrp32fZ6x5R4YHA"
WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def send_markdown(text):
    """通过 wecom-cli 发送 markdown 消息"""
    content_json = json.dumps({"content": text}, ensure_ascii=False)
    cmd = ["wecom-cli", "message", "aibot", "send",
           "--chat-id", CHAT_ID, "--msg-type", "markdown",
           "--markdown", content_json]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        return False, r.stdout[:500] + r.stderr[:300]
    return True, r.stdout[:300]


def short_name(name):
    """门店名缩短：去掉品牌前缀和括号"""
    s = str(name)
    for prefix in ["F.LUX国际美妆集合店（", "F.LUX全球美妆集合店（", "F.LUX芙乐美妆初颜馆（", "国际美妆集合店（"]:
        s = s.replace(prefix, "")
    s = s.replace("（", "").replace("）", "").replace("(", "").replace(")", "")
    return s


def fmt_money(v, decimals=0):
    if v is None:
        return "--"
    if decimals == 0:
        return "¥{:,.0f}".format(v)
    return "¥{:,.2f}".format(v)


def fmt_pct(v, signed=True):
    if v is None:
        return "--"
    if signed:
        return "{:+.1f}%".format(v * 100)
    return "{:.1f}%".format(v * 100)


def date_display(date_str):
    """日期显示：9月17日 周四"""
    if not date_str or len(date_str) < 8:
        return date_str
    y, m, d = int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8])
    dt = datetime(y, m, d)
    return f"{m}月{d}日 {WEEKDAYS[dt.weekday()]}"


def build_summary(d, date_str):
    """从简报JSON构造企微通知（对齐用户标准范式）"""
    cur = d.get("cur_agg", {})
    prev = d.get("prev_agg", {})
    date_disp = date_display(date_str or d.get("date", ""))

    def chg(k):
        c, p = cur.get(k), prev.get(k)
        if c is None or p in (None, 0):
            return None
        return (c - p) / p

    # 计算补贴率
    revenue = cur.get("营业额") or 0
    subsidy = cur.get("商家补贴金额") or 0  # 可能不在cur_agg中
    # 从佣金等字段推算，如果没有就不显示
    subsidy_rate = None

    L = []
    # ===== 标题 =====
    L.append(f"**老大，每日经营简报已生成！（数据日期 {date_disp}）**")
    L.append("")
    L.append("")

    # ===== 📊 核心数据 =====
    L.append("📊 **核心数据**")
    L.append("")

    # 营业额
    rev_chg = chg('营业额')
    rev_line = f"· 营业额：{fmt_money(cur.get('营业额'))}"
    if rev_chg is not None:
        rev_line += f"（环比{fmt_pct(rev_chg)}，前日 {fmt_money(prev.get('营业额'))}）"
    L.append(rev_line)
    L.append("")

    # 净收入
    net_chg = chg('净收入')
    net_line = f"· 净收入：{fmt_money(cur.get('净收入'))}"
    if net_chg is not None:
        net_line += f"（环比{fmt_pct(net_chg)}）"
    L.append(net_line)
    L.append("")

    # 有效订单
    ord_chg = chg('有效订单')
    ord_line = f"· 订单数：{int(cur.get('有效订单') or 0)} 单"
    if ord_chg is not None:
        diff = int(cur.get('有效订单') or 0) - int(prev.get('有效订单') or 0)
        ord_line += f"（环比{fmt_pct(ord_chg)}，{'少' if diff < 0 else '多'} {abs(diff)} 单）"
    L.append(ord_line)
    L.append("")

    # 客单价
    L.append(f"· 客单价：{fmt_money(d.get('客单价'), 2)}")
    L.append("")

    # 活跃门店
    total = d.get('total_stores', '--')
    active = int(cur.get('活跃门店') or 0)
    open_rate = f"{active/total*100:.0f}%" if total and isinstance(total, (int, float)) else "--"
    L.append(f"· 活跃门店：{active}/{total}（开张率 {open_rate}）")
    L.append("")

    # 取消率
    cancel_rate = d.get('取消率')
    if cancel_rate is not None:
        health = "健康" if cancel_rate < 0.05 else "偏高"
        L.append(f"· 取消率：{fmt_pct(cancel_rate, signed=False)}（{health}）")
        L.append("")

    L.append("")

    # ===== 🏆 门店 TOP3 =====
    top3 = d.get("store_top_revenue", [])[:3]
    if top3:
        L.append("🏆 **门店 TOP3**")
        L.append("")
        total_rev = sum(s.get("营业额", 0) for s in top3)
        for i, s in enumerate(top3, 1):
            name = short_name(s.get("名称", ""))
            rev = fmt_money(s.get("营业额"))
            orders = int(s.get("订单数") or s.get("订单") or 0)
            # 计算占比
            pct = f"（一店占 {s.get('营业额',0)/revenue*100:.0f}%）" if i == 1 and revenue else ""
            if orders:
                L.append(f"{i}. {name} {rev}（{orders} 单{pct}）")
            else:
                L.append(f"{i}. {name} {rev}{pct}")
            L.append("")
        L.append("")

    # ===== ⚠️ 异常与风险 =====
    L.append("⚠️ **异常与风险**")
    L.append("")
    risks = []

    # 取消率异常
    if cancel_rate and cancel_rate > 0.05:
        risks.append(f"取消率 {fmt_pct(cancel_rate, signed=False)} 超5%警戒线，需核实履约")

    # 零订单门店
    zero_names = d.get("zero_store_names", [])
    if zero_names:
        zero_short = "、".join(short_name(n) for n in zero_names[:4])
        risks.append(f"零订单门店 {len(zero_names)} 家：{zero_short}")

    # 高取消率门店
    if d.get("store_cancel_top"):
        for sc in d["store_cancel_top"][:2]:
            if sc.get("取消率", 0) > 0.08:
                risks.append(f"{short_name(sc.get('名称',''))}取消率 {fmt_pct(sc.get('取消率'), signed=False)}，核查履约")

    # 售后异常
    aft_amount = d.get("aft_amount")
    if aft_amount and aft_amount > 500:
        risks.append(f"售后退款 {fmt_money(aft_amount)}，需关注售后原因")

    # 评价异常
    eva_bad = d.get("eva_bad_cnt")
    if eva_bad:
        risks.append(f"{eva_bad} 条差评/低分评价，需及时回复处理")

    # 流量异常（曝光高但下单少）
    flow = d.get("flow_cur", {})
    if flow.get("曝光") and flow.get("曝光", 0) > 1000 and flow.get("下单", 0) == 0:
        risks.append(f"流量曝光 {flow['曝光']} 但 0 下单，流量转化异常")

    if not risks:
        risks.append("暂无重大异常，运营平稳")

    for r_ in risks[:5]:
        L.append(f"· {r_}")
        L.append("")
    L.append("")

    # ===== 💡 今日行动 =====
    L.append("💡 **今日行动**")
    L.append("")
    actions = []

    if cancel_rate and cancel_rate > 0.05:
        actions.append("当日核实高取消率门店共性（配送慢/缺货/拒单），目标取消率压回5%以下")
    if zero_names:
        actions.append(f"排查 {len(zero_names)} 家零订单店上架可见性与营业状态")
    if d.get("store_cancel_top"):
        actions.append(f"核查 {short_name(d['store_cancel_top'][0].get('名称',''))} 等门店取消原因")
    if eva_bad:
        actions.append(f"{eva_bad} 条差评当日回复清零，维护门店评分")
    if not actions:
        actions.append("维持当前运营节奏，关注环比变化趋势")

    for i, a in enumerate(actions[:4], 1):
        circle = ["①", "②", "③", "④", "⑤"][i-1]
        L.append(f"{circle} {a}")
        L.append("")
    L.append("")

    # ===== 📄 报告位置 =====
    L.append("📄 报告已存飞书「产出物/每日经营简报/」")
    L.append("")
    L.append("⏰ 下一个任务：今晚20点每日复盘")

    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", help="简报JSON文件路径")
    ap.add_argument("--send", help="直接发送的自定义文本")
    ap.add_argument("--date", default="")
    args = ap.parse_args()

    if args.send:
        ok, msg = send_markdown(args.send)
        print("发送成功" if ok else f"发送失败: {msg}")
        sys.exit(0 if ok else 1)

    if args.summary:
        with open(args.summary, encoding="utf-8") as f:
            d = json.load(f)
        text = build_summary(d, args.date or d.get("date", ""))
        print("=== 待发送内容 ===")
        print(text)
        print("=== 直接发送（定时任务场景）===")
        ok, msg = send_markdown(text)
        print("发送成功" if ok else f"发送失败: {msg}")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
