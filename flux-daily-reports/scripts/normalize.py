# -*- coding: utf-8 -*-
"""逐表规整：19 张采集表 → 17 张入库表。

依据 [整合与清洗规则](../10-事实基准/整合与清洗规则.md)（**已通过第三方审核**）。

## 五条不变量（写代码时不许破）

| # | 不变量 | 实测依据 |
|---|---|---|
| 1 | **空值绝不填 0** | `-` / `--` 是「没有数据」；填 0 就把「没卖」写成「卖了 0 元」 |
| 2 | **带符号 ≠ 数值**，必须**整串匹配** | 实测 `top80%商品缺勤损失` 含 `%`、`速度很快，我咨询了一下…` 含「分」 |
| 3 | **`∞` → 空值 + 标「不可比」** | 实测 **1229 处**（商品流量三表 + 配送指标明细），上期为 0 导致 |
| 4 | **`pp` 原样存数值**，列名加 `_pp` | 百分点是独立量纲，`2.10pp` → `2.10`，**不是 `0.021`** |
| 5 | **金额四舍五入到分** | 实测浮点噪声 `2353.5899999999997`，不修就入库脏数 |

## 输入输出

    python scripts/normalize.py                       # 规整全部
    python scripts/normalize.py --table 品类分析      # 只规整一张

- 输入：牵牛花 `牵牛花已下载表_v2/<表>/*.xlsx`（排除 `_ch*` 拆分版）+ 闪购 `out/rebuild/by_table/*.csv`
- 输出：`out/normalize/<表>.csv`（**不入库**）+ `out/normalize/_已排除表.json`
"""
import argparse
import csv
import glob
import json
import os
import re
import sys
from decimal import Decimal, ROUND_HALF_UP

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QN_DIR = os.path.join(ROOT, "牵牛花已下载表_v2")
SG_DIR = os.path.join(ROOT, "out", "rebuild", "by_table")
OUT_DIR = os.path.join(ROOT, "out", "normalize")
STORE_MASTER = os.path.join(ROOT, "10-事实基准", "门店主数据", "store-master.json")

SUMMARY_COLS = "_是否汇总行"
NOT_COMPARABLE = "不可比"

QIANNIU_TABLES = [
    "品类分析", "商品流量_店内", "商品流量_店外", "商品流量_近30天", "配送指标明细",
    "配送距离分布", "门店商品指标明细", "门店流量_分来源", "门店流量排行",
    "门店经营指标", "门店营业时长",
]
SHANGOU_TABLES = [
    "商品数据", "问题订单数据", "流量明细(新)", "流量渠道明细(新)",
    "评价数据", "售后订单数据", "门店财务明细", "门店成交明细",
]
TABLES = QIANNIU_TABLES + SHANGOU_TABLES          # 19

# 两张不入库（用户 2026-09-27 裁决），**仍采集**，只记「已排除 + 原因」
EXCLUDED_TABLES = {
    "门店流量排行": "用户 2026-09-27 裁决：整表不入库（继续采集，仅保留原始留档）",
    "门店流量_分来源": "牵牛花侧无门店列（维度是流量来源）；闪购「流量渠道明细(新)」已是门店口径，覆盖了它",
}

# 🔴 表头行数**逐表声明**，禁止用一个固定值 —— 这条被审核更正过两次（B2/B3）
# 实现者 2026-09-28 逐表读文件实测复核，与实施计划 Task 2.2 完全一致
HEADER_ROWS = {
    "商品流量_近30天": 3, "商品流量_店内": 3, "商品流量_店外": 3,
    "品类分析": 2, "配送指标明细": 2, "配送距离分布": 2, "门店商品指标明细": 2,
    "门店经营指标": 1, "门店流量排行": 1, "门店流量_分来源": 1, "门店营业时长": 1,
    "商品数据": 1, "问题订单数据": 1, "流量明细(新)": 1, "流量渠道明细(新)": 1,
    "评价数据": 1, "售后订单数据": 1, "门店财务明细": 1, "门店成交明细": 1,
}

# 六张**含合计行**的入库表 → 合计行判据（列名, 取值）
SUMMARY_TABLES = {
    "品类分析": ("一级品类", "汇总"),
    "配送指标明细": ("门店名称", "汇总"),
    "商品流量_近30天": ("商品信息", "汇总"),
    "商品流量_店内": ("商品信息", "汇总"),
    "商品流量_店外": ("商品信息", "汇总"),
    "门店商品指标明细": ("门店id", "-999"),      # 🔴 这张的标志是 -999，不是「汇总」
}

# 🔴 金额列**逐表声明**。
#    绝不能用列名关键词猜：`商品毛利率`、`品类销售额占比` 名字里带「金额/销售额」，
#    但是**比率** —— 对它们做「四舍五入到分」会把 0.7702 变成 0.77，**直接毁数据**。
#    本表由实现者 2026-09-28 逐表读真实列名 + 对照规则 §4.1/§4.4 各表清单核定。
MONEY_COLS = {
    "品类分析": {"商品原价销售额（含退款）", "总销售收入", "商品毛利额", "商品缺勤损失"},
    "门店经营指标": {"有效订单金额", "客单价", "净利润（账单口径）", "线上毛利", "线下其他",
                     "订单实付金额（剔除退款）", "订单实付客单价", "商品原价销售额(剔除退款）",
                     "有效订单包装费", "用户支付平台配送费", "售罄缺勤损失"},
    "门店商品指标明细": {"商品原价销售额（含退款）", "总销售收入", "商品原价销售额(剔除退款）",
                         "商品销售收入（剔除退款）", "商品实付金额", "订单退款金额",
                         "日均品类销售额", "商家商品优惠", "商家整单优惠", "商家单品优惠",
                         "平台商品补贴", "商品总补贴金额", "商家补贴金额", "平台补贴金额"},
    "配送指标明细": {"配送超时退款金额", "配送态度问题退款金额", "三方配送费", "三方基础配送费",
                     "三方配送小费", "履约服务费", "单均三方配送费", "单均三方基础配送费",
                     "单均三方配送小费", "单均履约服务费"},
    "门店财务明细": {"订单总金额", "订单折扣后金额", "平台承担活动金额", "商家承担活动金额",
                     "商品原价销售额", "商品实付销售额", "商品总补贴金额", "商品商家补贴金额",
                     "商品平台补贴金额", "订单小额付款", "配送费", "打包费"},
    "门店成交明细": {"商品实付金额", "商品原价", "包装费", "配送费"},
    "问题订单数据": {"订单总金额（元）", "退款金额（元）"},
    "评价数据": {"订单总金额（元）", "订单折扣后金额（元）"},
    "售后订单数据": {"订单总金额（元）"},
    "商品数据": {"订单总金额", "订单折扣后金额", "平台承担活动金额", "商家承担活动金额",
                 "商品原价销售额", "商品实付销售额", "商品总补贴金额", "商品商家补贴金额",
                 "商品平台补贴金额", "订单小额付款", "配送费", "打包费"},
}

# 🔴 列名重命名（规则 §4.1/§4.4 各表清单写明的**唯一几处**，不自行发挥）
RENAMES = {
    "一级品类": "品类",
    "商品信息": "商品名称",
    "门店名称": "门店",
    "店铺名称": "门店",
    "商家名称": "门店",
    "日期": "业务日期",
    "开始日期": "业务日期",
    "开始时间": "业务日期",
    "评价提交日期": "业务日期",
}
# 🔴 「环比波动」列的量纲**按指标类型分**（规则 §4.4.7 / §4.4.9 同一条）：
#    · **比率类**（指标名含「率」）→ 是**百分点**，原样存数值 + 列名加 `_pp` 后缀
#    · **计数类**（曝光人数/点击人数/下单人数等）→ 是**百分数**，转小数
#    判据用「指标名是否含『率』」—— 与规则里的「计数 `%` / 比率 `pp`」一一对应。
RATIO_MARK = "率"
PP_SUFFIX = "_pp"


# 🔴 所有「按列名查表」的常量，键都必须做**同样的括号归一** —— 否则全角写法的条目
#    **永远查不中，而且不报错**（静默）。实测**踩过两次**：
#      · `商品原价销售额（含退款）` → 金额没被四舍五入，`1603.9899999999998` 原样入库
#      · `订单商品点踩率（%）`     → 百分数没除以 100，`100` 原样入库（放大 100 倍）
#    `tests/test_normalize.py` 里有一条通用断言逐表扫这两组常量，防止再犯。
def output_colname(col):
    """原始列名 → 输出列名（含重命名与 `_pp` 后缀）。"""
    name = RENAMES.get(col, col)
    if col.endswith("_环比波动") and RATIO_MARK in col[: -len("_环比波动")]:
        return name + PP_SUFFIX
    return name


# 🔴 闪购各表的**业务日期取哪一列**（规则 §4.4.1~§4.4.5 逐表写明）
#    ⚠️ `商品数据` 的 `日期` 是**导出区间**（`20260801-20260831`），拿去当业务日期
#       会把 31 天压成 1 天 —— 规则明确要**丢弃**它、改用 `下单时间`。
#    值是**候选列的有序列表**：取第一个非空的。
#    ⚠️ `售后订单数据` 的 `售后发起时间` 实测 **183/728 行是空的**（规则也标了「166 行空」），
#       只取它会有 183 行拿不到业务日期 —— 故**回落到 `下单时间`**（实测 728/728 全有）。
#       这是该表自己的两个日期列（规则 §六 的列表现已并列写明），不是跨表借列。
BUSINESS_DATE_COL = {
    "商品数据": ("下单时间",), "问题订单数据": ("下单时间",),
    "售后订单数据": ("售后发起时间", "下单时间"),
    "流量明细(新)": ("日期",), "流量渠道明细(新)": ("日期",),
    "评价数据": ("评价提交日期",),
    "门店财务明细": ("开始时间",), "门店成交明细": ("开始日期",),
}
# 🔴 要**丢弃**的非业务列（规则 §二 第 10 条「纯序号列丢弃」+ 各表清单标的 ❌）
DROP_COLS = {
    "商品数据": {"日期"},          # 是导出区间，不是业务日期
    "门店经营指标": {"序号"}, "门店营业时长": {"序号"},
    # 🔴 R46（2026-09-28 用户裁决「甲」授权补）：规则明写「丢弃」但原先漏声明，
    #    这 4 列**静默留在了产物里**。判据都是「与另一列逐行相同」或「整列无值」。
    "门店财务明细": {"结束时间"},                                      # 规则 §4.1.2 第 2 列
    "门店成交明细": {"结束日期", "资质类型(仅药品)", "资质有效期(仅药品)"},  # 规则 §4.3.2
}
BATCH_COL = "_采集日期"            # 闪购批次标记 —— **丢弃**（规则 §4.4.x 各表清单）

# 来源三列（规则 §二 第 9 条）+ 渠道口径（第 13 条）+ 业务日期（第 1 条）
SRC_COLS = ("业务日期", "数据平台", "数据表", "采集时间", "渠道口径")

# 渠道口径（规则 §二 的「渠道口径对照」实测表）—— 本任务只取**非 `_ch` 版**，
# 故牵牛花侧一律「全渠道」。**只标口径，不替下游选口径。**
PLATFORM_OF = lambda t: "牵牛花" if t in QIANNIU_TABLES else "闪购"
CHANNEL_SCOPE_OF = lambda t: "美团闪购单渠道" if t in SHANGOU_TABLES else "全渠道"

# 🔴 真百分数、但**值里不带 % 号**的列（% 写在列名里）—— 规则 §二 第 3 条 / §4.4.4 第 19 列：
#    「评价数据」的 `订单商品点踩率（%）` 实测 `100`/`75`/`0` —— 是**真百分数**，
#    套「闪购已是小数」会放大 100 倍；不转换则 100 倍偏大。**逐列核过再判。**
PERCENT_NUMERIC_COLS = {("评价数据", "订单商品点踩率（%）")}

def _norm_col(c):
    return c.replace("（", "(").replace("）", ")")


# ⚠️ 必须放在 MONEY_COLS 与 PERCENT_NUMERIC_COLS **都定义之后**（第一版放前面，NameError）
MONEY_COLS = {t: {_norm_col(c) for c in cols} for t, cols in MONEY_COLS.items()}
PERCENT_NUMERIC_COLS = {(t, _norm_col(c)) for t, c in PERCENT_NUMERIC_COLS}


# 已知门店身份列（数据字典 §六 实测）
_ID_KEYS = ("店铺ID", "商家ID", "门店ID", "门店id")
_NAME_KEYS = ("门店名称", "店铺名称", "门店")
# ⚠️ 大小写两种写法都要认：历史基线是小写 `门店id`，2026-10-01 真机实测平台已改为
#    大写 `门店ID`。只认小写会让新增的「门店ID」列走到「按闪购商家ID查」分支，
#    在闪购映射里查不到 → 整行匹配失败（EMPTY_KEY_VALUE）。
#    （sync_store_master.py 里有一份同名常量，改这里时那边也要同步。）
QIANNIU_ID_KEYS = ("门店id", "门店ID")

_MONEY_KW = ("金额", "销售额", "收入", "费", "客单价", "毛利", "损失", "优惠", "补贴", "退款")
_DURATION_RE = re.compile(r"^(-?\d+(?:\.\d+)?)(秒|分钟|分|小时|时|天)$")
_PERCENT_RE = re.compile(r"^(-?\d+(?:\.\d+)?)%$")
_PP_RE = re.compile(r"^(-?\d+(?:\.\d+)?)pp$", re.I)
_NUM_RE = re.compile(r"^-?\d{1,3}(?:,\d{3})+(?:\.\d+)?$|^-?\d+(?:\.\d+)?$")

# `float64` 能**精确**表示的整数上限（2**53）。超过它的纯整数转浮点必失真 —— R46。
_FLOAT_EXACT_MAX = 2 ** 53
_NOT_A_NUMBER = ("∞", "-∞", "inf", "-inf", "infinity", "nan", "null", "none")


# ---------------- 纯函数 ----------------
def _clean(v):
    return "" if v is None else str(v).strip()


def parse_number(text):
    """数值文本 → 数值；空值符号与**任何非纯数值** → `None`。

    🔴 **整串匹配**，不是「含数字就抠出来」：实测 `top80%商品缺勤损失`（标签）、
    `28.5333分`（带单位）都不是数值 —— 抠出来会得到 80 和 28.5333 两个**假数**。

    🔴 **纯整数超过 `2**53` 时返回整数、不经过浮点**（R46 修复，2026-09-28 用户裁决「甲」）——
    `float64` 只能精确表示到 `2**53`；19 位订单号 / SKU 一转浮点就**丢末位**：
    实测 `'3002237140461947744'` → `'3.002237140461948e+18'`，
    于是 `1975406963517038625` 与 `…626`（**是两个不同商品**）变成同一个值，
    去重据此**误合 4 行**（销量少 5、实付少 44.66，审核方独立复算）。
    这类值**构造上就对不上号，只能保真**。

    ⚠️ 只动「会失真的那一类」—— 未超上限的数值仍走浮点，格式不变。
    """
    s = _clean(text).replace("　", "")
    if not s or s in ("-", "--"):
        return None
    if s.lower() in _NOT_A_NUMBER:            # ∞ 是「上期为 0 算不出来」，不是大数
        return None
    if not _NUM_RE.match(s):
        return None
    plain = s.replace(",", "")
    try:                                       # 只有**纯整数**才进得来（带小数点/指数会抛）
        exact = int(plain)
    except ValueError:
        exact = None
    # [F.LUX 变更] 原实现只对 `> 2**53` 的整数走保真分支，其余走 float，
    #   于是 `'1'` → `1.0` → `str` 得到 `'1.0'`，把**整数列**（门店ID/数量/有效订单数）
    #   的写法改坏了（自检 VALUE_MISMATCH/LONG_ID_LOSS 实测命中）。
    #   纯整数文本一律返回 int：值相同、文本形态与原件一致，且天然保真。
    if exact is not None:
        return exact
    try:
        return float(plain)
    except ValueError:
        return None


def parse_percent(text):
    """`'8.16%'` → `0.0816`（规则 §5.2 第 5 条：存小数）。

    🔴 必须整串是 `<数字>%` —— 实测 `top80%商品缺勤损失` 含 `%` 但是**标签**。
    """
    s = _clean(text)
    m = _PERCENT_RE.match(s)
    return float(m.group(1)) / 100.0 if m else None


def parse_pp(text):
    """`'2.10pp'` → `2.10`。

    🔴 **不转小数**：百分点是独立量纲，转成 0.021 是错的（规则 §4.4.9 陷阱一）。
    """
    s = _clean(text)
    m = _PP_RE.match(s)
    return float(m.group(1)) if m else None


def parse_duration_with_unit(text):
    """`'4秒'` → `4.0`；`'35.4167分'` → `35.4167`（规则 §5.2 第 10 条：去单位转数值）。

    🔴 **必须整串是 `<数字><单位>`**：实测评价内容
    `速度很快，我咨询了一下，自送十分钟不到就送到了…` 含「分」但是**文本**。
    """
    s = _clean(text)
    m = _DURATION_RE.match(s)
    return float(m.group(1)) if m else None


def round_money(x):
    """金额 → `Decimal`，四舍五入到分（规则 §二 规则 5）。

    实测牵牛花金额带浮点噪声（`2353.5899999999997`），不修就会入库成脏数。
    """
    if isinstance(x, str):
        v = parse_number(x)
        if v is None:
            return None
        x = v
    if x is None:
        return None
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def normalize_colname(s):
    """列名归一：去首尾空白、括号全半角统一（`（）` → `()`）。"""
    out = _clean(s).replace("（", "(").replace("）", ")")
    return out.strip()


def is_summary_row(row, table):
    """合计行判定 —— **逐表**给判据，不靠「首列等于某个词」一刀切。

    实测：`品类分析`/`配送指标明细` 的标志是「汇总」；**`门店商品指标明细` 是 `-999`**；
    而 `配送距离分布`、`门店经营指标` **根本没有合计行**（首行就是门店）。
    """
    rule = SUMMARY_TABLES.get(table)
    if not rule:
        return False
    col, val = rule
    for k, v in row.items():
        if normalize_colname(k) == normalize_colname(col):
            return _clean(v) == val
    return False


def to_date(value):
    """把日期值归一成 `YYYY-MM-DD`。支持 `20260804`、`2026-08-08 10:41:34`、`2026-08-08`。"""
    s = _clean(value)
    if not s:
        return ""
    m = re.match(r"^(\d{4})[-/.]?(\d{2})[-/.]?(\d{2})", s)
    return "%s-%s-%s" % (m.group(1), m.group(2), m.group(3)) if m else ""


def parse_cell(value, col, table):
    """把一个单元格按**列的类型**转成该有的值。

    顺序：空值 → 百分比 → 百分点 → 带单位时长 → 金额 → 普通数值 → 原样文本。
    """
    s = _clean(value)
    if not s or s in ("-", "--") or s.lower() in _NOT_A_NUMBER:
        return "" if s.lower() not in _NOT_A_NUMBER else NOT_COMPARABLE
    if (table, col) in PERCENT_NUMERIC_COLS:      # 真百分数（% 写在列名里，值不带号）
        num = parse_number(s)
        return "" if num is None else num / 100.0
    if col in MONEY_COLS.get(table, ()):
        money = round_money(s)
        return "" if money is None else str(money)
    for fn in (parse_percent, parse_pp, parse_duration_with_unit):
        got = fn(s)
        if got is not None:
            return got
    num = parse_number(s)
    return num if num is not None else s


# ---------------- 读表 ----------------
def _expand_merges(ws, n_rows):
    """把合并单元格的值**填满它覆盖的范围**（否则只有左上角有值，其余是 None）。

    🔴 不展开就必然错位：`商品流量_店内` 的 `A1:L1`（商品信息横跨 12 列）不展开时
    只有 col0 有值，而 col1~col11 在第 2 行另有名字 —— 按「非空值顺序」拼出来的列名
    会**整体左移**（实测把 `商品曝光人数_本期` 配到了错误的列上）。
    """
    grid = [[None] * ws.max_column for _ in range(n_rows)]
    for r in range(n_rows):
        for c in range(ws.max_column):
            v = ws.cell(row=r + 1, column=c + 1).value
            grid[r][c] = _clean(v)
    raw = [list(r) for r in grid]        # ⚠️ 判「占位层」要用**填充前**的值
    spans = {}
    for rng in ws.merged_cells.ranges:
        v = grid[rng.min_row - 1][rng.min_col - 1] if rng.min_row <= n_rows else ""
        for r in range(rng.min_row - 1, min(rng.max_row, n_rows)):
            for c in range(rng.min_col - 1, rng.max_col):
                grid[r][c] = v
        if rng.min_row == 1:
            spans[rng.min_col - 1] = (rng.max_col - rng.min_col + 1, v)
    return grid, spans, raw


def _split_header(grid, spans, raw):
    """合并感知的表头解析 → 列名列表。

    规则（**由实测的合并结构反推，并与规则 §4.4 各表清单逐表核对**）：

    1. 某一层**所有列取值相同**（如品类分析第 2 行全是「本期」）→ 该层是占位、**丢掉**
    2. 合并组下一层给出的名字数 **等于**组宽 → 组名是**父名**，拼成 `组_子`
    3. 合并组下一层给出的名字数 **少一个**（如「商品信息」横跨 12 列、下层 11 个名字）
       → 组名是**第一列自己的名字**，其余列直接用下层名字（不拼父名）
    """
    n = len(grid)
    width = len(grid[0])
    # 1. 丢掉「整层只有一个取值」的占位层 —— ⚠️ 用**填充前**的 raw 判，
    #    否则纵向合并（如 `A1:A2` = 一级品类）会让第 2 行看起来有两个取值，占位层丢不掉
    keep = []
    for r in range(n):
        vals = {v for v in raw[r] if v}
        if r > 0 and len(vals) == 1:
            continue
        keep.append(r)
    names = []
    for c in range(width):
        parts = []
        for r in keep:
            v = grid[r][c]
            if v and (not parts or parts[-1] != v):    # 相邻重复去掉（纵向合并会给出同一个值）
                parts.append(v)
        if not parts:
            names.append("col%d" % c)
            continue
        names.append("_".join(parts))
    # 2/3. 处理合并组的父子拼接
    for start, (span, group) in sorted(spans.items()):
        if not group or span <= 1:
            continue
        below = [grid[1][c] for c in range(start, min(start + span, width))] if n > 1 else []
        below = [b for b in below if b and b != group]
        if not below:
            continue
        names[start] = group
        if len(below) == span:                      # 组名是父名 → 全部拼成 组_子
            for i in range(span):
                names[start + i] = "%s_%s" % (group, below[i])
        else:                                        # 组名是第一列自己的名字
            for i, b in enumerate(below):
                names[start + 1 + i] = b
    return [normalize_colname(x) for x in names]


def read_qianniu(table):
    """读牵牛花一张表 → `[(业务日期, 列名, 数据行), ...]`，**每个原始业务日一份**。

    🔴 **必须读全部按日文件**（第 1 轮审核阻塞项 B1）：
    原先写 `sorted(fs)[-1]` 只取**最后一份**，于是 56 天的数据只规整出 1 天 ——
    实测 `门店经营指标` 有 56 份（8/1~9/25），产物却只有 50 行。
    牵牛花的表**没有日期列**（规则 §二 规则 1：日期只在文件名上），
    所以**业务日期只能从文件名取**。

    排除 `_ch*` 渠道拆分版（本任务只取非渠道版；`渠道口径` 如实标「全渠道」）。
    """
    import openpyxl
    fs = [p for p in glob.glob(os.path.join(QN_DIR, table, "*.xlsx"))
          if "_ch" not in os.path.basename(p)]
    out = []
    for path in sorted(fs):
        m = re.search(r"_(\d{8})\.xlsx$", os.path.basename(path))
        if not m:
            continue
        day = to_date(m.group(1))
        ws = openpyxl.load_workbook(path, data_only=True).active
        n = HEADER_ROWS[table]
        grid, spans, raw = _expand_merges(ws, n)
        hdr = _split_header(grid, spans, raw)
        data = [list(r) for r in ws.iter_rows(min_row=n + 1, values_only=True)]
        data = [r + [None] * (len(hdr) - len(r)) for r in data]
        out.append((day, hdr, [[_clean(c) for c in r[:len(hdr)]] for r in data]))
    return out


def read_shangou(table):
    """读闪购一张表（MDP-TASK-202 重建产物 CSV）→ `[(None, 列名, 数据行)]`。

    闪购是**一张表一个文件**（各批次已并在一起），业务日期**逐行取**（见 `BUSINESS_DATE_COL`）。
    """
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    from shangou_merge import read_csv_text
    p = os.path.join(SG_DIR, table + ".csv")
    if not os.path.exists(p):
        return []
    hdr, data = read_csv_text(p)
    return [(None, [normalize_colname(c) for c in hdr], data)]


# ---------------- 门店标准ID ----------------
def load_store_index():
    """读 MDP-TASK-201 的字典 → `(闪购ID映射, 归一店名索引, 已知标准ID集)`。"""
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    from sync_store_master import build_id_mapping, build_name_index, normalize_store_name
    if not os.path.exists(STORE_MASTER):
        raise SystemExit("缺门店字典：%s —— 先跑 scripts/sync_store_master.py" % STORE_MASTER)
    stores = json.load(open(STORE_MASTER, encoding="utf-8"))["stores"]
    return build_id_mapping(stores), build_name_index(stores), normalize_store_name


def match_store_row(row, id_map, name_idx, normalize_name):
    """一行 → `门店标准ID`；**匹配不上返回 `None`，不猜**（ADR-0009）。"""
    known = set(id_map.values()) | set(name_idx.values())
    for k in _ID_KEYS:
        v = _clean(row.get(k))
        if not v:
            continue
        if k in QIANNIU_ID_KEYS:
            # 「门店ID/门店id」列有两种语义，**先查闪购商家ID映射**：
            #   闪购 gen2 流量表（流量明细(新)/流量渠道明细(新)）的「门店ID」列
            #   实际存的是**闪购商家ID**（8 位，如 27184856），命中 id_map；
            #   牵牛花表的「门店id/门店ID」是**门店标准ID**（7 位，如 1157860），
            #   不在 id_map 的 key 里，走 known 校验。两套编号不撞号，先查 id_map
            #   可同时正确命中两平台。
            hit = id_map.get(v)
            if hit:
                return hit
            return v if v in known else None
        return id_map.get(v)
    for k in _NAME_KEYS:
        v = _clean(row.get(k))
        if v:
            return name_idx.get(normalize_name(v))
    return None


def normalize_table(table, id_map, name_idx, normalize_name):
    """规整一张表（**含全部原始业务日**）→ `(列名, 行, 统计)`。

    输出列 = 业务列（去掉非业务列、按名重命名） + `业务日期`/`数据平台`/`数据表`/`采集时间`/`渠道口径`
    （规则 §二 第 1、9、13 条） + `_是否汇总行` + `门店标准ID`。
    """
    batches = read_qianniu(table) if table in QIANNIU_TABLES else read_shangou(table)
    if not batches:
        return None, None, {"跳过": "没有找到原始文件"}
    drop = DROP_COLS.get(table, set())
    date_cols = BUSINESS_DATE_COL.get(table, ())
    out, unmatched, summary = [], 0, 0
    cols = None
    days = set()
    for file_day, hdr, rows in batches:
        biz_cols = [c for c in hdr if c not in drop and c != BATCH_COL]
        if cols is None:
            # 🔴 有的表规则要求把日期列**重命名**成 `业务日期`（如流量明细的 `日期`）——
            #    那种情况就**不再新增**一列，否则出现**两个同名列**（实测 5 张表中招）。
            have_biz_date = any(output_colname(c) == "业务日期" for c in biz_cols)
            src = [c for c in SRC_COLS if not (c == "业务日期" and have_biz_date)]
            cols = [output_colname(c) for c in biz_cols] + src + [SUMMARY_COLS, "门店标准ID"]
        for r in rows:
            raw = dict(zip(hdr, r))
            if not any(_clean(x) for x in r):
                continue
            # ⚠️ 判定（汇总行、门店匹配）用**原始列名**；输出才用重命名后的列名
            rec = {output_colname(c): parse_cell(raw.get(c), c, table) for c in biz_cols}
            # 业务日期：牵牛花从**文件名**取；闪购从**该表的日期列**取（规则 §二 第 1 条）
            if file_day:                       # 牵牛花：日期只在文件名上
                day = file_day
            else:                              # 闪购：逐个候选列取第一个非空的
                day = next((to_date(raw.get(c)) for c in date_cols if to_date(raw.get(c))), "")
            rec["业务日期"] = day or ""
            rec["数据平台"] = PLATFORM_OF(table)
            rec["数据表"] = table
            # 采集时间：闪购的批次标记（丢弃前先取走它的值）；牵牛花就是文件日期
            rec["采集时间"] = _clean(raw.get(BATCH_COL)) or file_day or ""
            rec["渠道口径"] = CHANNEL_SCOPE_OF(table)
            is_sum = is_summary_row(raw, table)
            rec[SUMMARY_COLS] = 1 if is_sum else 0
            if is_sum:
                summary += 1
            sid = match_store_row(raw, id_map, name_idx, normalize_name)
            if sid is None and any(_clean(raw.get(k)) for k in _ID_KEYS + _NAME_KEYS):
                unmatched += 1
            rec["门店标准ID"] = sid or ""
            days.add(rec["业务日期"])
            out.append(rec)
    return cols, [[rec.get(c, "") for c in cols] for rec in out], \
        {"行": len(out), "汇总行": summary, "门店未匹配": unmatched,
         "业务日数": len([d for d in days if d])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", help="只规整一张表")
    ap.add_argument("--out", default=OUT_DIR)
    args = ap.parse_args()

    id_map, name_idx, normalize_name = load_store_index()
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "_已排除表.json"), "w", encoding="utf-8") as fo:
        json.dump([{"表": t, "原因": r} for t, r in EXCLUDED_TABLES.items()],
                  fo, ensure_ascii=False, indent=1)

    todo = [args.table] if args.table else [t for t in TABLES if t not in EXCLUDED_TABLES]
    print("%-18s %8s %8s %8s" % ("表", "行数", "汇总行", "门店未匹配"))
    total_un = 0
    for t in todo:
        cols, rows, st = normalize_table(t, id_map, name_idx, normalize_name)
        if cols is None:
            print("%-18s   —— %s" % (t, st["跳过"])); continue
        with open(os.path.join(args.out, t + ".csv"), "w", encoding="utf-8-sig", newline="") as fo:
            w = csv.writer(fo); w.writerow(cols); w.writerows(rows)
        total_un += st["门店未匹配"]
        print("%-18s %8d %8d %8d" % (t, st["行"], st["汇总行"], st["门店未匹配"]))
    print("\n已排除（仍采集、不入库）：%s" % "、".join(EXCLUDED_TABLES))
    print("门店未匹配合计：%d 行" % total_un)
    return 0


if __name__ == "__main__":
    sys.exit(main())
