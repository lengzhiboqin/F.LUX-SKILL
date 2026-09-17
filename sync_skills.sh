#!/usr/bin/env bash
# F.LUX-SKILL 同步脚本：检测本地技能变化 → 同步到仓库 → 提交推送
# 用法: bash sync_skills.sh [--dry-run]
set -euo pipefail

# 配置
SKILLS_SRC="/home/user/.doubao/agent_mode/workspace/.user_skills"
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
DRY_RUN=false
if [ "${1:-}" = "--dry-run" ]; then
  DRY_RUN=true
fi

# 需要同步的技能目录（白名单，避免同步其他项目的技能）
SKILLS=("shangou-data-pipeline" "flux-daily-briefing")

# 排除模式（不复制这些文件/目录）
EXCLUDE_PATTERNS=(
  "data/"
  "__pycache__/"
  "*.pyc"
  "config.json"
  "*.log"
  "*.xlsx"
  "*.csv"
)

echo "=========================================="
echo " F.LUX-SKILL 同步脚本"
echo " 源目录: $SKILLS_SRC"
echo " 仓库目录: $REPO_DIR"
echo " 模式: $([ "$DRY_RUN" = true ] && echo "干跑(只检测不修改)" || echo "执行同步")"
echo "=========================================="
echo ""

CHANGED=false

for skill in "${SKILLS[@]}"; do
  SRC="$SKILLS_SRC/$skill"
  DST="$REPO_DIR/$skill"

  if [ ! -d "$SRC" ]; then
    echo "⚠️  跳过 $skill: 源目录不存在"
    continue
  fi

  echo "📦 检查技能: $skill"

  # 创建目标目录
  mkdir -p "$DST"

  # 使用 rsync 同步（排除指定模式）
  RSYNC_ARGS=(-a --delete)
  for pat in "${EXCLUDE_PATTERNS[@]}"; do
    RSYNC_ARGS+=(--exclude="$pat")
  done

  if [ "$DRY_RUN" = true ]; then
    RSYNC_ARGS+=(--dry-run --itemize-changes)
  fi

  OUTPUT=$(rsync "${RSYNC_ARGS[@]}" "$SRC/" "$DST/" 2>&1) || true

  if [ -n "$OUTPUT" ]; then
    echo "$OUTPUT" | while IFS= read -r line; do
      echo "   $line"
    done
    CHANGED=true
    echo "   ✅ 有变化"
  else
    echo "   ✅ 无变化"
  fi
  echo ""
done

# 检测新技能（源目录有但白名单没有的）
echo "🔍 检测新技能目录..."
for dir in "$SKILLS_SRC"/*/; do
  name=$(basename "$dir")
  # 跳过白名单内的
  SKIP=false
  for s in "${SKILLS[@]}"; do
    if [ "$name" = "$s" ]; then
      SKIP=true
      break
    fi
  done
  if [ "$SKIP" = false ]; then
    # 检查是否有 SKILL.md（确认为技能目录）
    if [ -f "$dir/SKILL.md" ]; then
      echo "   ⚠️  发现新技能: $name（未在同步白名单中，请手动添加到 sync_skills.sh 的 SKILLS 数组）"
    fi
  fi
done
echo ""

# Git 提交推送
if [ "$CHANGED" = true ] && [ "$DRY_RUN" = false ]; then
  echo "📝 检测到变化，执行 git 提交..."
  cd "$REPO_DIR"

  git add -A
  if git diff --cached --quiet; then
    echo "   无实际文件变化（可能仅时间戳不同），跳过提交"
  else
    COMMIT_MSG="sync: 更新技能 $(date '+%Y-%m-%d %H:%M')"
    git commit -m "$COMMIT_MSG"
    echo "   ✅ 已提交: $COMMIT_MSG"

    echo "🚀 推送到 GitHub..."
    if git push origin main 2>&1; then
      echo "   ✅ 推送成功"
    else
      echo "   ❌ 推送失败，请检查网络和权限"
      exit 1
    fi
  fi
elif [ "$CHANGED" = false ]; then
  echo "✅ 所有技能已是最新，无需同步"
fi

echo ""
echo "=========================================="
echo " 同步完成"
echo "=========================================="
