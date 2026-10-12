# -*- coding: utf-8 -*-
"""测试包引导：把 `scripts/` 加入 `sys.path`，使 `common` / `run_daily` 等可导入。

在任何 `tests.*` 模块导入前执行；这样从技能包根目录跑
`python -m unittest discover -s tests` 也能工作。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SCRIPTS = os.path.join(_ROOT, "scripts")
for _p in (_ROOT, _SCRIPTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

REFERENCES = os.path.join(_ROOT, "references")
