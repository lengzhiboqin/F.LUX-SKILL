#!/usr/bin/env bash
# 闪购数据管线 — 安装引导（薄包装，跨平台路径全在Python处理）
exec python3 "$(dirname "$0")/setup_wizard.py" "$@"
