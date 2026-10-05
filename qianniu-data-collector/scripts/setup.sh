#!/bin/bash
# 牵牛花数据采集 Skill - 安装向导入口
# 用法: bash setup.sh [--check|--show|--reset]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "============================================"
echo "  牵牛花数据采集 Skill · 安装向导"
echo "============================================"
echo ""

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "❌ 未找到 python3，请先安装 Python 3.8+"
    exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "✅ Python 版本: $PY_VERSION"

# 检查必需包
echo ""
echo "检查必需 Python 包..."
MISSING_PKGS=""
for pkg in requests openpyxl pandas; do
    if ! python3 -c "import $pkg" 2>/dev/null; then
        MISSING_PKGS="$MISSING_PKGS $pkg"
    fi
done

if [ -n "$MISSING_PKGS" ]; then
    echo "⚠️  缺失包:$MISSING_PKGS"
    read -p "是否自动安装？[Y/n] " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        echo "正在安装..."
        python3 -m pip install $MISSING_PKGS
        echo "✅ 安装完成"
    else
        echo "⚠️  跳过安装，部分功能可能不可用"
    fi
else
    echo "✅ 所有必需包已安装"
fi

echo ""
echo "启动交互式安装向导..."
echo ""

python3 "$SCRIPT_DIR/setup_wizard.py" "$@"
