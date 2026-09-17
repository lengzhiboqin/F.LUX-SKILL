#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F.LUX每日经营简报 · 企业微信通知（v3）

格式说明（重要）：
  企微 markdown 中单换行（\n）会粘连成一行，必须用**空行**（\n\n）分隔每条，
  确保每条独立成行、一屏可读。结构参考：标题 → 📊核心数据 → 🏆TOP3 → ⚠️异常 → 💡行动。

用法：
  python3 notify_wecom.py --summary <简报JSON> [--date 20260915]
"""
import argparse, json, os, subprocess, sys

CHAT_ID = "woeC4LQgAAk8Ci2f7Xrp32fZ6x5R4YHA"


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
    s = s.replace("F.LUX国际美妆集合店（", "").replace("F.LUX全球美妆集合店（", "")
    s = s.replace("F.LUX芙乐美妆初颜馆（", "").replace("（", "").replace("）", "").replace("(", "").replace(")", "")
    return s


def fmt_money(v):
    if v is None:
        return "--"
    return "¥{:,.2f}".format(v)


def fmt_pct(v, signed=True):
    if v is None:
        return "--"
    if signed:
        return "{:+.1f}%".format(v * 100)
    return "{:.1f}%".format(v * 100)


def build_summary(d, date_str):
    """从简报JSON构造企微通知（每条之间空行分隔）"""
    cur = d.get("cur_agg", {})
    prev = d.get("prev_agg", {})
    date_disp = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}" if date_str else "--"

    def chg(k):
        c, p = cur.get(k), prev.get(k)
        if c is None or p in (None, 0):
            return None
        return (c - p) / p

    L = []
    # 标题
    L.append(f"**老大，F.LUX每日经营简报已生成（{date_disp}）**")
    L.append("")
    L.append("")

    # 📊 核心数据
    L.append("📊 **核心数据**")
    L.append("")
    L.append(f"· 营业额：{fmt_money(cur.get('营业额'))}（环比{fmt_pct(chg('营业额'))}，前日{fmt_money(prev.get('营业额'))}）")
    L.append("")
    L.append(f"· 净收入：{fmt_money(cur.get('净收入'))}（环比{fmt_pct(chg('净收入'))}）")
    L.append("")
    L.append(f"· 有效订单：{int(cur.get('有效订单') or 0)}单（环比{fmt_pct(chg('有效订单'))}，前日{int(prev.get('有效订单') or 0)}单）")
    L.append("")
    L.append(f"· 客单价：{fmt_money(d.get('客单价'))}")
    L.append("")
    L.append(f"· 活跃门店：{int(cur.get('活跃门店') or 0)}/{d.get('total_stores', '--')}家")
    L.append("")
    L.append(f"· 取消率：{fmt_pct(d.get('取消率'), signed=False)}（健康阈值<5%）")
    L.append("")
    L.append("")

    # 🏆 门店 TOP3
    top3 = d.get("store_top_revenue", [])[:3]
    if top3:
        L.append("🏆 **门店 TOP3**")
        L.append("")
        for i, s in enumerate(top3, 1):
            L.append(f"{i}. {short_name(s.get('名称',''))}：{fmt_money(s.get('营业额'))}（{int(s.get('订单') or 0)}单，客单{fmt_money(s.get('客单价'))}）")
            L.append("")
        L.append("")

    # ⚠️ 异常与风险
    L.append("⚠️ **异常与风险**")
    L.append("")
    risks = []
    if d.get("取消率") and d["取消率"] > 0.05:
        risks.append(f"取消率{fmt_pct(d['取消率'], signed=False)}超5%警戒线")
    zero_names = d.get("zero_store_names", [])
    if zero_names:
        risks.append(f"零订单门店{len(zero_names)}家：{'、'.join(short_name(n) for n in zero_names[:3])}")
    if d.get("store_cancel_top"):
        for sc in d["store_cancel_top"][:2]:
            risks.append(f"{short_name(sc.get('名称',''))}取消率{fmt_pct(sc.get('取消率'), signed=False)}")
    if d.get("eva_bad_cnt"):
        risks.append(f"{d['eva_bad_cnt']}家门店差评（评分≤2）")
    if not risks:
        risks.append("暂无重大异常")
    for r_ in risks[:4]:
        L.append(f"· {r_}")
        L.append("")
    L.append("")

    # 💡 今日行动
    L.append("💡 **今日行动**")
    L.append("")
    actions = []
    if d.get("取消率") and d["取消率"] > 0.05:
        actions.append("核实高取消率门店履约（缺货/配送/拒单），压回5%以下")
    if zero_names:
        actions.append("排查零订单门店上架与曝光，确认是否暂停营业")
    if d.get("store_cancel_top"):
        actions.append(f"核查{short_name(d['store_cancel_top'][0].get('名称',''))}等门店取消原因")
    if d.get("store_top_net") and d["store_top_net"][0].get("净利率", 0) > 0.5:
        topn = d["store_top_net"][0]
        actions.append(f"复盘{short_name(topn.get('名称',''))}高净利率打法（净利率{fmt_pct(topn.get('净利率'), signed=False)}）向门店推广")
    if not actions:
        actions.append("维持当前运营节奏，关注环比变化")
    for i, a in enumerate(actions[:4], 1):
        L.append(f"{i}. {a}")
        L.append("")
    L.append("")

    # 📄 报告位置
    L.append("📄 详细简报已上传飞书`产出物/每日经营简报/`")
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
