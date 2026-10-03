# -*- coding: utf-8 -*-
"""门店主数据**每日同步** + `门店标准ID` 归位。

依据 [ADR-0009](../10-事实基准/ADR/0009-store-master-sync.md)：

| 约束 | 为什么 |
|---|---|
| **以 ID 为准**，店名只做精确匹配 | 店名是「导出时间点的快照」，改名后按名统计会把一家店拆成两家 |
| **改名 → 旧名进 `aliases`** | 让「唯一化」成为同步的副产品，而不是额外人工 |
| **ID 消失 → 只标 `inactive`，不删** | 闭店门店的历史数据仍需正确归属 |
| **匹配不上 → `None`**，绝不猜 | 猜错的代价是把数据错分到别家店 |

## `门店标准ID` 取什么值

**取牵牛花 `门店ID`（接口的 `storeId`）**。理由：

1. 主数据的权威来源就是**牵牛花**「设置 → 门店管理」，`storeId` 是它自己的主键
2. **每家店都有** —— 闪购渠道可以没开（实测 50 家里 1 家没有）
3. 牵牛花自带 ID 的表（`门店商品指标明细.门店id`）**可以直接命中，不用映射**

> ⚠️ 本文件、任务书与 [ADR-0009](../10-事实基准/ADR/0009-store-master-sync.md) 都只写了
> `master_id` / `门店标准ID` 这个名字，**没有明文规定取哪个平台的 ID**。
> 上表是实现者据 ADR「以 ID 为准 + 牵牛花是主数据源」推定的，**已在执行报告里列为待审核项**。

## 输入

| 路径 | 何时用 |
|---|---|
| **接口** `POST /core/poi/b/store/page-list`（`pageSize=100` 分页） | **自动化同步走这条** |
| **页面导出** `10-事实基准/门店主数据/导出门店信息_<YYYYMMDD>.xlsx` | 接口不可用时兜底 / 离线复算 |

## 用法

    python scripts/sync_store_master.py                       # 接口优先，失败走最近一次导出
    python scripts/sync_store_master.py --from-export <xlsx>  # 只用指定导出文件（离线）
    python scripts/sync_store_master.py --check               # 只报告不写盘
"""
import argparse
import glob
import json
import os
import re
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MASTER_DIR = os.path.join(ROOT, "10-事实基准", "门店主数据")
STORE_MASTER = os.path.join(MASTER_DIR, "store-master.json")
HISTORY_DIR = os.path.join(MASTER_DIR, "history")
MAPPING_MD = os.path.join(ROOT, "10-事实基准", "门店映射_标准ID.md")
UNMATCHED_MD = os.path.join(ROOT, "10-事实基准", "门店映射_未匹配.md")
EXCEPTIONS = os.path.join(MASTER_DIR, "门店例外台账.json")

# 例外门店的标准ID前缀 —— **必须加前缀**：闪购门店ID与牵牛花门店ID是两套编号，
# 不加前缀将来一旦撞号，会把两家不同的店悄悄并成一家。
EXCEPTION_PREFIX = "SG-"

STORE_LIST_API = "https://qnh.meituan.com/core/poi/b/store/page-list"

# 渠道 ID（Phase 0 结论 0.4 实测：100=美团闪购，200=饿了么）
SHANGOU_CHANNEL_ID = 100
# 门店状态（2026-09-28 实测：与页面导出的「门店状态」列 50/50 完全一致）
_STATUS_TEXT = {1: "active", 2: "inactive"}

# 各表承载门店身份/名称的列（数据字典 §六 实测）
ID_KEYS = ("店铺ID", "商家ID", "门店ID", "门店id")
NAME_KEYS = ("门店名称", "店铺名称", "门店")
# 牵牛花自带的门店ID 与主数据同源，可直接当标准 ID —— 但要先确认它真是主数据里的店。
# ⚠️ 大小写两个写法都要认：历史基线里是小写 `门店id`，2026-10-01 真机实测平台已改为
#    大写 `门店ID`。只认小写会让新增的「门店ID」列落到「按闪购商家ID查」的分支，
#    而闪购映射里查不到它 → 整行匹配失败（EMPTY_KEY_VALUE）。
QIANNIU_ID_KEYS = ("门店id", "门店ID")
# 合计行的店名关键字 —— **只用来写「原因」，不参与匹配判断**
# （匹配仍然只看「在不在字典里」，不因此改变任何一行是否匹配的结果）
AGGREGATE_KEYWORDS = ("汇总", "合计", "总计", "小计", "全部", "所有")

_FULL2HALF = {"（": "(", "）": ")", "·": ".", "．": "."}


# ---------------- 纯函数 ----------------
def clean(v):
    return "" if v is None else str(v).strip()


def normalize_store_name(name):
    """店名归一：去首尾空白与全角空格、全角括号转半角、间隔号转点。

    🔴 **只做这些**，不做模糊匹配 —— ADR-0009 已作废「按店名模糊匹配」。
    实测要处理的是：门店名称用全角 `（）`、渠道门店名称用半角 `()`、`F·LUX` 与 `F.LUX`。
    """
    s = clean(name).replace("　", "")
    for a, b in _FULL2HALF.items():
        s = s.replace(a, b)
    return s.strip()


def status_of(code):
    """接口 `storeStatus` → `active` / `inactive`。未知值一律 `active`（不擅自判停用）。"""
    try:
        return _STATUS_TEXT.get(int(code), "active")
    except (TypeError, ValueError):
        return "active"


def parse_store_page(payload, day):
    """接口一页 → 记录列表（`store-master.json` 的 `stores[]` 形状）。"""
    out = []
    for o in ((payload or {}).get("data") or {}).get("list") or []:
        sid = clean(o.get("storeId"))
        if not sid:
            continue
        channels = o.get("channelStores") or []
        sg = [c for c in channels if c.get("channelId") == SHANGOU_CHANNEL_ID]
        ids, names = [], []
        for c in sg:
            pid, pname = clean(c.get("channelPoiId")), clean(c.get("channelPoiName"))
            if pid and pid not in ids:
                ids.append(pid)
            if pname and pname not in names:
                names.append(pname)
        out.append({
            "master_id": sid,
            "qianniu": {"poi_id": sid, "name": clean(o.get("storeName")), "aliases": []},
            "shangou": {"merchant_ids": ids, "names": names},
            "status": status_of(o.get("storeStatus")),
            "scope": "both",
            "first_seen": day,
            "last_seen": day,
        })
    return out


def by_id(records):
    return {r["master_id"]: r for r in records}


def diff_versions(prev, curr):
    """两版主数据的**四态**对比（ADR-0009「决定」一）。

    | 结果 | 含义 |
    |---|---|
    | `added` | 出现新 ID → 新开门店 |
    | `renamed` | 同一 ID、店名变了 → 旧名进 `aliases[]` |
    | `inactive` | ID 消失 → 闭店，**只标不删** |
    | `unchanged` | ID 与名字都没变 |
    """
    added = sorted(set(curr) - set(prev))
    gone = sorted(set(prev) - set(curr))
    renamed, unchanged = [], []
    for mid in sorted(set(prev) & set(curr)):
        old = prev[mid].get("qianniu", {}).get("name", "")
        new = curr[mid].get("qianniu", {}).get("name", "")
        if normalize_store_name(old) != normalize_store_name(new):
            renamed.append({"master_id": mid, "old": old, "new": new})
        else:
            unchanged.append(mid)
    return {"added": added, "renamed": renamed, "inactive": gone, "unchanged": unchanged}


def merge_aliases(prev_aliases, renamed):
    """把改名前的旧名**追加**进 `aliases`（只增不改，可追溯）。"""
    out = {k: list(v) for k, v in (prev_aliases or {}).items()}
    for r in renamed or []:
        mid, old = r.get("master_id"), r.get("old")
        lst = out.setdefault(mid, [])
        if old and old not in lst:
            lst.append(old)
    return out


def load_exceptions(master_dir=MASTER_DIR):
    """读**门店例外台账**（用户人工判断过的「对不上号」门店）。

    🔴 为什么需要它：挂靠门店 / 无人仓门店**永远不会有**牵牛花侧记录 ——
    不建档的话，每天的审计都会把它们重新报一遍「未匹配」，真问题会被噪音淹掉。
    """
    p = os.path.join(master_dir, os.path.basename(EXCEPTIONS))
    if not os.path.exists(p):
        return []
    try:
        return json.loads(open(p, encoding="utf-8").read())
    except (ValueError, OSError):
        return []


def exception_records(ledger, day):
    """例外台账 → 主数据记录（`scope=shangou_only`）。

    给它们一个 `SG-<闪购门店ID>` 标准ID —— 这样它们的闪购数据**仍能按店统计**，
    又不会跟牵牛花那套编号混在一起。
    """
    out = []
    for e in ledger or []:
        sid = clean(e.get("shangou_id"))
        if not sid:
            continue
        out.append({
            "master_id": EXCEPTION_PREFIX + sid,
            "qianniu": {"poi_id": None, "name": "", "aliases": []},
            "shangou": {"merchant_ids": [sid], "names": [clean(e.get("name_in_data"))]},
            "status": "inactive" if e.get("category") == "已倒闭" else "active",
            "scope": "shangou_only",
            "exception": {"category": clean(e.get("category")), "reason": clean(e.get("reason")),
                          "note": clean(e.get("note")), "judged_by": clean(e.get("judged_by")),
                          "judged_at": clean(e.get("judged_at"))},
            "first_seen": clean(e.get("judged_at")) or day,
            "last_seen": day,
        })
    return out


def shangou_only_ids(records):
    return {r["master_id"] for r in records if r.get("scope") == "shangou_only"}


def build_id_mapping(records):
    """`美团闪购渠道门店ID → 门店标准ID`。"""
    out = {}
    for r in records:
        for pid in r.get("shangou", {}).get("merchant_ids", []):
            out[pid] = r["master_id"]
    return out


def alias_conflicts(records):
    """一个归一后的店名指向**多个** `门店标准ID` → 逐条列出，**不替用户选**。

    覆盖两种撞名：两家店的现名相同；或某家的**旧名**被另一家当成了现名。
    """
    seen = defaultdict(set)
    for r in records:
        mid = r["master_id"]
        names = [r.get("qianniu", {}).get("name", "")] + list(r.get("qianniu", {}).get("aliases") or [])
        for n in names:
            key = normalize_store_name(n)
            if key:
                seen[key].add(mid)
    return [{"name": k, "master_ids": sorted(v)}
            for k, v in sorted(seen.items()) if len(v) > 1]


def build_name_index(records):
    """`归一店名 → 门店标准ID`（含 `aliases`）。

    ⚠️ **有歧义的名字整个排除** —— 宁可返回 `None` 进未匹配清单，也不猜一个给下游。
    """
    seen = defaultdict(set)
    for r in records:
        for n in [r.get("qianniu", {}).get("name", "")] + list(r.get("qianniu", {}).get("aliases") or []):
            key = normalize_store_name(n)
            if key:
                seen[key].add(r["master_id"])
    return {k: next(iter(v)) for k, v in seen.items() if len(v) == 1}


def match_store(row, id_mapping, name_index):
    """一行数据 → `门店标准ID`；**匹配不上返回 `None`**（ADR-0009「决定」三）。

    顺序：**有 ID 用 ID**（闪购侧）→ 牵牛花自带 `门店id`（需确认真是主数据里的店）
    → 都没有才按**归一后的店名精确匹配**。**任何情况都不返回近似值。**
    """
    if not row:
        return None
    known = set(id_mapping.values()) | set(name_index.values())

    for k in ID_KEYS:
        v = clean(row.get(k))
        if not v:
            continue
        if k in QIANNIU_ID_KEYS:
            # 「门店ID/门店id」列有两种语义，**先查闪购商家ID映射**：
            #   闪购 gen2 流量表（流量明细(新)/流量渠道明细(新)）的「门店ID」列
            #   实际存的是**闪购商家ID**（8 位，如 27184856），命中 id_mapping；
            #   牵牛花表的「门店id/门店ID」是**门店标准ID**（7 位，如 1157860），
            #   不在 id_mapping 的 key 里，走 known 校验。两套编号不撞号。
            hit = id_mapping.get(v)
            if hit:
                return hit
            # 牵牛花的 `门店id` 与主数据同源，但实测同一列里有 `-999` 这类占位符
            # （`门店商品指标明细` 50 个取值中 1 个）—— 不在主数据里就不认
            return v if v in known else None
        if v in id_mapping:
            return id_mapping[v]
        return None                      # 有 ID 但查不到 → 不退回按名字猜

    for k in NAME_KEYS:
        v = clean(row.get(k))
        if not v:
            continue
        return name_index.get(normalize_store_name(v))
    return None


def unmatched_summary(rows, id_mapping, name_index):
    """未匹配清单（按门店聚合）—— **每条都带「原值 + 列 + 行数 + 原因」**。

    任务书 §四 第 8 条要求「每条都有『原值 + 原因』」：只说「通常是汇总行」不算数，
    要能逐条看出**这一条为什么没匹配上**、核对得了。
    """
    agg = {}
    for row in rows:
        if match_store(row, id_mapping, name_index) is not None:
            continue
        col = ident = ""
        for k in ID_KEYS + NAME_KEYS:
            v = clean(row.get(k))
            if v:
                col, ident = k, v
                break
        if not ident:
            continue                      # 纯空行不列（不是门店，是表尾的空行）
        a = agg.setdefault(ident, {"原值": ident, "列": col, "行数": 0,
                                   "原因": _unmatched_reason(col, ident)})
        a["行数"] += 1
        for nk in ("店铺名称", "门店名称", "商家名称"):
            v = clean(row.get(nk))
            if v:
                a.setdefault("数据里的名字", set()).add(v)
    out = []
    for a in agg.values():
        if isinstance(a.get("数据里的名字"), set):
            a["数据里的名字"] = " / ".join(sorted(a["数据里的名字"]))
        out.append(a)
    return sorted(out, key=lambda x: -x["行数"])


def _unmatched_reason(col, val):
    """给一条未匹配写出**可核对**的原因（不是「通常如何」这种笼统说法）。"""
    if col in ID_KEYS:
        if val.startswith("-") or not val.isdigit():
            return "`%s` 列的值 %s 不是有效门店ID（占位符）" % (col, val)
        return "门店ID %s 不在主数据里（未建档的新店，或闭店后已从名录移除）" % val
    for kw in AGGREGATE_KEYWORDS:
        if kw in val:
            return "合计行（`%s` 列含「%s」），不是门店" % (col, kw)
    return "店名「%s」不在主数据（含历史别名）里" % val


def unmatched_report(rows, id_mapping, name_index):
    """未匹配清单：**逐条给出原值与原因**，不静默丢弃。"""
    out = []
    for row in rows:
        if match_store(row, id_mapping, name_index) is not None:
            continue
        ident = ""
        for k in ID_KEYS + NAME_KEYS:
            v = clean(row.get(k))
            if v:
                ident = "%s=%s" % (k, v)
                break
        reason = "该行没有门店ID列，且店名不在主数据（含别名）里" if "=" in ident and ident.split("=")[0] in NAME_KEYS \
            else "门店ID不在主数据里（可能是占位符或未同步的新店）"
        out.append({"原值": ident or "(空行)", "原因": reason})
    return out


def summarize(diff):
    """变更清单（给用户看的一句话版）。"""
    return ("新增 %d 家 ｜ 改名 %d 家 ｜ 停用 %d 家 ｜ 无变化 %d 家"
            % (len(diff["added"]), len(diff["renamed"]),
               len(diff["inactive"]), len(diff["unchanged"])))


def render_mapping_md(records, diff=None, conflicts=(), day=""):
    """人可读对账表（入库）。"""
    L = ["# 门店映射 · 标准ID（自动生成，勿手改）",
         "",
         "> 由 `scripts/sync_store_master.py` 生成。依据 [ADR-0009](ADR/0009-store-master-sync.md)。",
         "> 源：牵牛花「设置 → 门店管理」接口 `store/page-list`（与页面导出同源）。",
         "",
         "**`门店标准ID` = 牵牛花门店ID（`storeId`）** —— 每家店都有，且牵牛花自带 ID 的表可直接命中。",
         "",
         "导出日：%s ｜ 门店 **%d** 家" % (day, len(records)),
         ""]
    if diff:
        L += ["变更：%s" % summarize(diff), ""]
    main = [r for r in records if r.get("scope", "both") == "both"]
    only = [r for r in records if r.get("scope") == "shangou_only"]

    L += ["## 一、牵牛花侧门店（两边都有）", "",
          "| 门店标准ID | 门店名称（牵牛花） | 别名（历史名） | 美团闪购门店ID | 状态 |",
          "|---|---|---|---|---|"]
    for r in sorted(main, key=lambda x: (x["status"] != "active", x["master_id"])):
        L.append("| %s | %s | %s | %s | %s |" % (
            r["master_id"],
            r.get("qianniu", {}).get("name", ""),
            "、".join(r.get("qianniu", {}).get("aliases") or []) or "—",
            "、".join(r.get("shangou", {}).get("merchant_ids") or []) or "—",
            {"active": "启用", "inactive": "停用"}.get(r["status"], r["status"])))
    L += ["", "## 二、例外门店（**仅闪购侧**，用户已逐家判断）", "",
          "> 🔴 这些店**只做闪购**、牵牛花中台里没有（也不用有）——",
          "> 它们的闪购数据照常按店统计，**但不能拿去跟牵牛花侧比**。",
          "> 标准ID 加 `SG-` 前缀，与牵牛花那套编号**分开，避免将来越号并店**。", ""]
    if not only:
        L.append("✅ 无。")
    else:
        L += ["| 门店标准ID | 数据里的店名 | 美团闪购门店ID | 类别 | 原因 | 状态 |",
              "|---|---|---|---|---|---|"]
        for r in sorted(only, key=lambda x: x["master_id"]):
            L.append("| %s | %s | %s | %s | %s | %s |" % (
                r["master_id"], "、".join(r["shangou"].get("names") or []) or "—",
                "、".join(r["shangou"].get("merchant_ids") or []) or "—",
                r.get("exception", {}).get("category", ""),
                r.get("exception", {}).get("reason", ""),
                {"active": "营业中", "inactive": "已停业"}.get(r["status"], r["status"])))
    if conflicts:
        L += ["", "## ⚠️ 别名冲突（同名指向多个 ID，**不自动选**）", "",
              "| 归一后店名 | 指向的标准ID |", "|---|---|"]
        L += ["| %s | %s |" % (c["name"], "、".join(c["master_ids"])) for c in conflicts]
    return "\n".join(L) + "\n"


# ---------------- I/O ----------------
def fetch_store_records(day, page_size=100):
    """走接口分页取全量。**在页面上下文内 fetch**，自动带上登录态与风控签名。"""
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    from common.cdp import QN

    qn = QN()
    records, page = [], 1
    while True:
        body = {"current": page, "pageSize": page_size, "channelList": [],
                "channelPoiStatusList": [], "costCentIds": [], "commercialAgentIds": [],
                "noCommercialAgentRelated": False, "page": page}
        js = '''(async () => {
          const r = await fetch(%s, {method:'POST', credentials:'include',
            headers:{'Content-Type':'application/json'}, body: JSON.stringify(%s)});
          return await r.text();
        })()''' % (json.dumps(STORE_LIST_API), json.dumps(body))
        payload = json.loads(qn.js(js))
        if payload.get("code") != 0:
            raise RuntimeError("门店列表接口返回 code=%s" % payload.get("code"))
        got = parse_store_page(payload, day)
        records.extend(got)
        data = payload.get("data") or {}
        if len(records) >= int(data.get("total") or 0) or not got:
            break
        page += 1
    return records


def read_export_xlsx(path, day):
    """读页面导出的 `导出门店信息_<YYYYMMDD>.xlsx`（兜底路径，列较少）。

    ⚠️ **不能用 `read_only=True`**：openpyxl 的只读模式依赖文件里声明的区域尺寸，
    而牵牛花导出的 xlsx 有的没写对 —— 实测 `门店经营指标_*_ch200.xlsx`（7 KB，有数据）
    在只读模式下 `max_row=1`，**只读出表头一行，静默丢光数据**。文件都很小，直接整读。
    """
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    rows = list(wb.active.iter_rows(values_only=True))
    if not rows:
        raise RuntimeError("导出文件没有内容：%s" % path)
    hdr = [clean(c) for c in rows[0]]
    if "门店ID" not in hdr:
        raise RuntimeError("导出文件没有「门店ID」列，字段可能变了：%s" % path)
    ix = {c: i for i, c in enumerate(hdr)}
    records = []
    for r in rows[1:]:
        sid = clean(r[ix["门店ID"]])
        if not sid:
            continue
        names = [clean(x) for x in clean(r[ix.get("渠道门店名称", -1)]).split(",") if clean(x)] \
            if "渠道门店名称" in ix else []
        ids = [clean(x) for x in clean(r[ix.get("渠道门店ID", -1)]).split(",") if clean(x)] \
            if "渠道门店ID" in ix else []
        chans = [clean(x) for x in clean(r[ix.get("渠道名", -1)]).split(",") if clean(x)] \
            if "渠道名" in ix else []
        # 渠道列是**位置对齐**的多值串 —— 只保留美团闪购那几项
        sg_ids, sg_names = [], []
        for i, c in enumerate(chans):
            if "美团闪购" in c:
                if i < len(ids) and ids[i]:
                    sg_ids.append(ids[i])
                if i < len(names) and names[i]:
                    sg_names.append(names[i])
        records.append({
            "master_id": sid,
            "qianniu": {"poi_id": sid, "name": clean(r[ix["门店名称"]]), "aliases": []},
            "shangou": {"merchant_ids": sg_ids, "names": sg_names},
            "status": "active" if clean(r[ix["门店状态"]]) == "启用" else "inactive",
            "scope": "both",
            "first_seen": day, "last_seen": day,
        })
    return records


def load_store_master(path=STORE_MASTER):
    if not os.path.exists(path):
        return {"stores": []}
    with open(path, encoding="utf-8") as fo:
        return json.load(fo)


def save_store_master(doc, path=STORE_MASTER):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fo:
        json.dump(doc, fo, ensure_ascii=False, indent=1)


def save_snapshot(doc, day, history_dir=HISTORY_DIR):
    """按日快照 —— 可回溯上一版，用于对比。"""
    os.makedirs(history_dir, exist_ok=True)
    p = os.path.join(history_dir, "store-master_%s.json" % day)
    with open(p, "w", encoding="utf-8") as fo:
        json.dump(doc, fo, ensure_ascii=False, indent=1)
    return p


def export_day_of(path):
    """从 `导出门店信息_<YYYYMMDD>.xlsx` 文件名取它**自己的日期** → `YYYY-MM-DD`。

    🔴 为什么必须取文件名而不是文件修改时间：这份文件是**人工从浏览器下载再移进来**的，
    mtime 是「移进来的时候」，不是「导出的那一天」。
    """
    m = re.search(r"(\d{4})(\d{2})(\d{2})\.xlsx$", os.path.basename(path or ""))
    return "%s-%s-%s" % (m.group(1), m.group(2), m.group(3)) if m else ""


def latest_export(master_dir=MASTER_DIR):
    """最近一次页面导出 → `(日期 ISO, 路径)`；没有则 `("", None)`。"""
    found = []
    for p in glob.glob(os.path.join(master_dir, "导出门店信息_*.xlsx")):
        d = export_day_of(p)
        if d:
            found.append((d, p))
    return sorted(found)[-1] if found else ("", None)


def resolve_source(api_fetch, day, from_export=None, master_dir=MASTER_DIR):
    """选输入源 → `(records, 来源说明, 数据自己的日期)`。

    顺序：**指定导出 > 接口 > 当天的页面导出 > 报错**。

    🔴 **回退只接受「当天」的页面导出**（第 1 轮审核阻塞项 B1）：
    原先只要目录里有导出文件就用「最新那份」，于是拿 9/26 的旧名录按 9/28 同步一遍，
    程序报「无变化 50 家」、还把 50 条 `last_seen` 写成 9/28 —— **旧数据被伪装成当天同步过**。
    这两天若有新店或改名，就被彻底漏掉。宁可失败，也不要这种假成功。
    """
    if from_export:
        eday = export_day_of(from_export) or day
        # `--from-export` 是操作者的**显式选择**，允许用非当天的文件 —— 但必须如实标注是哪天的
        src = _src_text(from_export, eday)
        return _check(read_export_xlsx(from_export, eday), src), src, eday
    try:
        records = api_fetch(day)
    except BaseException as e:
        err = str(e)[:120]
        eday, epath = latest_export(master_dir)
        if not epath:
            raise RuntimeError("接口失败（%s），且没有可用的页面导出文件 —— 不用旧映射继续" % err)
        if eday != day:
            raise RuntimeError(
                "接口失败（%s）；且没有 %s 当天的页面导出，目录里只有 %s 的旧留档 "
                "—— 旧名录不得冒充当天同步，退出，不用旧映射继续" % (err, day, eday))
        src = _src_text(epath, eday)
        return _check(read_export_xlsx(epath, eday), src), src, eday
    return _check(records, "接口 store/page-list"), "接口 store/page-list", day


def _src_text(path, eday):
    return "页面导出 %s（文件日期 %s）" % (os.path.basename(path), eday or "未知")


def _check(records, src):
    """拿到 0 家门店一律视为失败 —— 空表覆盖上去会把整份主数据抹掉。"""
    if not records:
        raise RuntimeError("%s 读到 0 家门店 —— 视为失败，不覆盖既有主数据" % src)
    return records


def apply_diff(prev_records, curr_records, day):
    """把四态对比的结果落到新一版记录上：新店登记、旧名进别名、消失的标 inactive。"""
    prev, curr = by_id(prev_records), by_id(curr_records)
    diff = diff_versions(prev, curr)
    aliases = merge_aliases({m: r.get("qianniu", {}).get("aliases") or [] for m, r in prev.items()},
                            diff["renamed"])
    out = []
    for mid, r in curr.items():
        r = json.loads(json.dumps(r))
        r["qianniu"]["aliases"] = aliases.get(mid, [])
        r["first_seen"] = prev.get(mid, {}).get("first_seen", day)
        r["last_seen"] = day
        out.append(r)
    for mid in diff["inactive"]:                 # 🔴 停用**不删** —— 历史数据还要归属
        r = json.loads(json.dumps(prev[mid]))
        r["status"] = "inactive"
        r["last_seen"] = prev[mid].get("last_seen", day)
        out.append(r)
    return out, diff


def read_shangou_rows(by_table_dir):
    """闪购重建产物的业务行（`out/rebuild/by_table/*.csv`）。"""
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    from shangou_merge import read_csv_text
    for p in sorted(glob.glob(os.path.join(by_table_dir, "*.csv"))):
        hdr, data = read_csv_text(p)
        for r in data:
            yield dict(zip(hdr, r))


def read_qianniu_rows(data_dir, day=None):
    """牵牛花当日各表的数据行（取每张表最新一份，`_ch*` 拆分版优先排除）。"""
    import openpyxl
    for d in sorted(glob.glob(os.path.join(data_dir, "*"))):
        if not os.path.isdir(d):
            continue
        cands = [p for p in glob.glob(os.path.join(d, "*.xlsx")) if "_ch" not in os.path.basename(p)]
        if not cands:
            continue
        p = sorted(cands)[-1]
        try:
            rr = list(openpyxl.load_workbook(p, data_only=True).active.iter_rows(values_only=True))
        except Exception:
            continue
        if not rr:
            continue
        hdr = [clean(c) for c in rr[0]]
        if not (set(hdr) & set(ID_KEYS + NAME_KEYS)):
            continue                      # 该表没有门店维度，跳过
        for r in rr[1:]:
            yield dict(zip(hdr, r))


def audit(stores, shangou_dir, qianniu_dir):
    """把主数据字典套到**当前真实数据**上，出未匹配清单。"""
    idm, nidx = build_id_mapping(stores), build_name_index(stores)
    sg = unmatched_summary(list(read_shangou_rows(shangou_dir)), idm, nidx)
    qn = unmatched_summary(list(read_qianniu_rows(qianniu_dir)), idm, nidx)
    return sg, qn


def render_unmatched_md(sg, qn, day, n_stores, exceptions=()):
    L = ["# 门店映射 · 未匹配清单（自动生成，勿手改）",
         "",
         "> 由 `scripts/sync_store_master.py --audit` 生成。",
         "> 口径：把 `门店标准ID` 字典套到**当前数据**上，列出**匹配不上**的门店。",
         "> 🔴 **不猜** —— 匹配不上就是匹配不上，由人工判断（[ADR-0009](ADR/0009-store-master-sync.md)）。",
         "",
         "生成日：%s ｜ 主数据门店 **%d** 家" % (day, n_stores),
         "",
         "## 一、闪购侧（自带门店ID，却不在主数据里）",
         ""]
    if not sg:
        L.append("✅ **无。** 数据里出现的闪购门店ID **全部**已在字典里 ——")
        L.append("包括用户 2026-09-28 逐家判断过的 5 家（见 §三），它们已按例外建档、不再算「未匹配」。")
    else:
        L += ["| 原值 | 取自哪一列 | 数据里的名字 | 行数 | 原因 |", "|---|---|---|---|---|"]
        L += ["| %s | `%s` | %s | %d | %s |"
              % (x["原值"], x.get("列", ""), x.get("数据里的名字", ""), x["行数"], x["原因"])
              for x in sg]
        L += ["", "> ⚠️ 这些是**真实门店**却不在字典里，也**不在例外台账**中 —— 需要人工判断：",
              "> 是新开的店、已闭店的店，还是**合并/改名后换了 ID**。判断后记入",
              "> [`门店主数据/门店例外台账.json`](门店主数据/门店例外台账.json)，下次同步即不再报。"]
    L += ["", "## 二、牵牛花侧（只有店名，匹配不上）", ""]
    if not qn:
        L.append("✅ 无。")
    else:
        L += ["| 原值 | 取自哪一列 | 行数 | 原因 |", "|---|---|---|---|"]
        L += ["| %s | `%s` | %d | %s |" % (x["原值"], x.get("列", ""), x["行数"], x["原因"])
              for x in qn]
        L += ["", "> ℹ️ 牵牛花侧匹配不上的通常是**汇总行 / 合计行 / 占位符**（实测：`汇总`、`全部`、`-999`），",
              "> 不是门店。**汇总行的识别与排除属于 Task 2.2（规整）的范围**，本任务只如实列出。"]
    L += ["", "## 三、已判断的例外门店（**不再计入未匹配**）", "",
          "> 来源：用户 2026-09-28 逐家判断，记于",
          "> [`门店主数据/门店例外台账.json`](门店主数据/门店例外台账.json)。", ""]
    if not exceptions:
        L.append("（无）")
    else:
        L += ["| 美团闪购门店ID | 标准ID | 数据里的店名 | 类别 | 原因 |",
              "|---|---|---|---|---|"]
        for r in sorted(exceptions, key=lambda x: x["master_id"]):
            L.append("| %s | `%s` | %s | %s | %s |" % (
                "、".join(r["shangou"].get("merchant_ids") or []), r["master_id"],
                "、".join(r["shangou"].get("names") or []) or "—",
                r.get("exception", {}).get("category", ""),
                r.get("exception", {}).get("reason", "")))
        L += ["", "**为什么给它们建 ID 而不是排除**：这 5 家的闪购数据是**真实营业额**"
                  "（实测共 2009 行），排除掉会让门店维度凭空少一块；",
              "给一个带 `SG-` 前缀的标准ID，**数据照常统计、又不会被错并到牵牛花门店上**。",
              "若将来决定不计入，按 `scope == \"shangou_only\"` 过滤即可 ——",
              "**这一步可逆，排除不可逆**。"]
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", help="同步日 YYYY-MM-DD（缺省今天）")
    ap.add_argument("--from-export", help="只用指定导出 xlsx（离线/兜底）")
    ap.add_argument("--check", action="store_true", help="只报告，不写盘")
    ap.add_argument("--audit", action="store_true",
                    help="把字典套到当前真实数据上，出未匹配清单")
    ap.add_argument("--shangou-dir", default=os.path.join(ROOT, "out", "rebuild", "by_table"))
    ap.add_argument("--qianniu-dir", default=os.path.join(ROOT, "牵牛花已下载表_v2"))
    ap.add_argument("--master", default=STORE_MASTER)
    args = ap.parse_args()

    from datetime import date
    day = args.day or date.today().isoformat()

    # 输入：指定导出 > 接口 > **当天的**页面导出 > 报错退出（不用旧映射静默继续）
    try:
        records, src, data_day = resolve_source(fetch_store_records, day, args.from_export)
    except (RuntimeError, OSError) as e:
        raise SystemExit(str(e))

    doc = load_store_master(args.master)
    prev = doc.get("stores") or []
    # 🔴 例外门店（挂靠/无人仓/已倒闭）**不参与四态比对** —— 它们本来就不在牵牛花名录里，
    #    参与比对每天都会被误判成「ID 消失」。它们由台账维护，同步时原样保留。
    prev_main = [r for r in prev if r.get("scope", "both") == "both"]
    # `last_seen` 记**数据自己的日期**，不是运行日 —— 否则用旧导出兜底时会伪装成当天见过
    merged, diff = apply_diff(prev_main, records, data_day)
    ledger = load_exceptions(os.path.dirname(args.master) or MASTER_DIR)
    exceptions = exception_records(ledger, day)
    merged = merged + exceptions
    for r in merged:
        r.setdefault("scope", "both")
    conflicts = alias_conflicts(merged)
    id_mapping = build_id_mapping(merged)
    name_index = build_name_index(merged)

    n_main = len(merged) - len(exceptions)
    print("来源：%s" % src)
    print("运行日 %s ｜ **数据日 %s**%s"
          % (day, data_day, "" if data_day == day else "   ⚠️ 用的不是当天数据，last_seen 按数据日记"))
    print("门店 %d 家（牵牛花侧 %d + 例外建档 %d；上一版牵牛花侧 %d）"
          % (len(merged), n_main, len(exceptions), len(prev_main)))
    print("变更：%s" % summarize(diff))
    if exceptions:
        print("   例外门店（用户已判断，仅闪购侧）：%d 家" % len(exceptions))
        for r in exceptions:
            print("      %-14s %s（%s）" % (r["master_id"], r["exception"]["category"],
                                          r["exception"]["reason"]))
    for mid in diff["added"]:
        print("   🆕 新增 %s %s" % (mid, by_id(merged)[mid]["qianniu"]["name"]))
    for r in diff["renamed"]:
        print("   ✏️ 改名 %s：%s → %s" % (r["master_id"], r["old"], r["new"]))
    for mid in diff["inactive"]:
        print("   ⏸ 停用 %s %s（保留，不删除）" % (mid, by_id(merged)[mid]["qianniu"]["name"]))
    if conflicts:
        print("   ⚠️ 别名冲突 %d 条（不自动选）：" % len(conflicts))
        for c in conflicts:
            print("      %s → %s" % (c["name"], "、".join(c["master_ids"])))
    print("门店标准ID 字典：闪购ID %d 个 ｜ 店名（含别名）%d 个"
          % (len(id_mapping), len(name_index)))

    if args.audit:
        print("\n=== 未匹配清单（把字典套到当前数据上）===")
        sg, qn = audit(merged, args.shangou_dir, args.qianniu_dir)
        print("闪购侧：%d 个门店ID 不在主数据里（共 %d 行）"
              % (len(sg), sum(x["行数"] for x in sg)))
        for x in sg:
            print("   %-12s %6d 行  %s" % (x["原值"], x["行数"], x.get("数据里的名字", "")))
        print("牵牛花侧：%d 条匹配不上" % len(qn))
        for x in qn[:12]:
            print("   %-10s %5d 行  [%s] %s" % (x["原值"][:10], x["行数"],
                                                x.get("列", ""), x["原因"]))
        if not args.check:
            with open(UNMATCHED_MD, "w", encoding="utf-8") as fo:
                fo.write(render_unmatched_md(sg, qn, day, len(merged), exceptions))
            print("\n已写：%s" % UNMATCHED_MD)

    if args.check:
        print("\n--check：未写盘。")
        return 0

    new_doc = {"version": day, "data_day": data_day, "generated_at": day,
               "source": src, "stores": merged}
    save_store_master(new_doc, args.master)
    snap = save_snapshot(new_doc, day)
    # 变更日志只记**真有变化**的 —— 否则同一天多跑几次会刷出一堆重复段落
    if diff["added"] or diff["renamed"] or diff["inactive"]:
        with open(os.path.join(MASTER_DIR, "变更日志.md"), "a", encoding="utf-8") as fo:
            fo.write("\n## %s（%s）\n\n- 门店 %d 家 ｜ %s\n" % (day, src, len(merged), summarize(diff)))
            for r in diff["renamed"]:
                fo.write("- ✏️ 改名 %s：%s → %s\n" % (r["master_id"], r["old"], r["new"]))
            for mid in diff["added"]:
                fo.write("- 🆕 新增 %s %s\n" % (mid, by_id(merged)[mid]["qianniu"]["name"]))
            for mid in diff["inactive"]:
                fo.write("- ⏸ 停用 %s（保留）\n" % mid)
    with open(MAPPING_MD, "w", encoding="utf-8") as fo:
        fo.write(render_mapping_md(merged, diff, conflicts, day))
    print("\n已写：%s\n     %s\n     %s" % (args.master, snap, MAPPING_MD))
    return 0


if __name__ == "__main__":
    sys.exit(main())
