# -*- coding: utf-8 -*-
"""19 表注册表：从 `references/table_registry.json` 读取并做一致性自检。

注册表是**采集/清洗的唯一表清单来源**：任何人不得在别处再写一份硬编码表名。
"""
import json
import os

DEFAULT_REGISTRY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "references", "table_registry.json")

REQUIRED_FIELDS = ("table", "platform", "type", "expected_cols")
ALLOWED_TYPES = frozenset(("daily", "event", "snapshot"))


def load_registry(path=None):
    """读注册表；缺字段/类型非法/表名重复即报错（宁可早失败）。"""
    p = path or DEFAULT_REGISTRY
    with open(p, encoding="utf-8") as f:
        reg = json.load(f)
    tables = reg.get("tables")
    if not tables:
        raise ValueError("注册表没有 tables：%s" % p)
    seen = set()
    for t in tables:
        for f in REQUIRED_FIELDS:
            if f not in t or t[f] in (None, ""):
                raise ValueError("注册表条目缺字段 %s：%r" % (f, t.get("table")))
        if t["type"] not in ALLOWED_TYPES:
            raise ValueError("注册表类型非法：%s -> %r" % (t["table"], t["type"]))
        if t["table"] in seen:
            raise ValueError("注册表表名重复：%s" % t["table"])
        seen.add(t["table"])
        if t["type"] != "snapshot" and not isinstance(t.get("default_offset_days"), int):
            raise ValueError("日表/事件表必须给 default_offset_days：%s" % t["table"])
    reg["_index"] = {t["table"]: t for t in tables}
    return reg


def get_table(reg, table):
    try:
        return reg["_index"][table]
    except KeyError:
        raise KeyError("不在 19 表注册表里：%s" % table)


def tables_of(reg, platform=None):
    return [t for t in reg["tables"] if platform is None or t["platform"] == platform]


def table_names(reg):
    return [t["table"] for t in reg["tables"]]


def check_complete(reg, expected_platforms=("牵牛花", "闪购"), expected_total=19):
    """注册表自检：19 张、两平台、名称不重复。"""
    n = len(reg["tables"])
    if n != expected_total:
        raise ValueError("注册表应有 %d 张表，实为 %d" % (expected_total, n))
    got = {t["platform"] for t in reg["tables"]}
    if not set(expected_platforms) <= got:
        raise ValueError("注册表缺平台：%s" % (set(expected_platforms) - got))
    return True
