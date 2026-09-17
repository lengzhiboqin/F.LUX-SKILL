#!/usr/bin/env bash
# 闪购数据管线 — 一键闭环（薄包装，所有逻辑在Python里处理跨平台路径）
exec python3 "$(dirname "$0")/run_pipeline.py"
