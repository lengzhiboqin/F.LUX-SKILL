#!/usr/bin/env bash
# F.LUX-SKILL 同步脚本（v3：自动发现技能 + 分支管理 + 质量门禁）
#
# 用法:
#   bash sync_skills.sh              # 自动扫描所有技能，检测变化，推送到 dev/<技能名> 分支
#   bash sync_skills.sh --dry-run    # 只检测，不修改
#   bash sync_skills.sh --status     # 查看各技能验证状态
#   bash sync_skills.sh --promote <技能名>  # 把 dev/<技能名> 合并到 main（需连续3天无报错）
#   bash sync_skills.sh --issue <技能名> "<标题>" "<内容>"  # 创建GitHub Issue（优化建议）
#
# 分支策略:
#   main              = 稳定主线（连续3天无报错才合并进来）
#   dev/<技能名>      = 验证区（新技能/有修改的技能先推到这里）
#
# v3变更: 取消白名单，自动扫描 .user_skills/ 下所有带 SKILL.md 的目录
#         新技能自动加入同步并推送到dev分支，同时通知用户
set -euo pipefail

# 配置
SKILLS_SRC="/home/user/.doubao/agent_mode/workspace/.user_skills"
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
STATUS_FILE="$HOME/.config/flux-skills/skill_status.json"
DRY_RUN=false
COMMAND="sync"  # sync | status | promote | issue
PROMOTE_SKILL=""
ISSUE_SKILL=""
ISSUE_TITLE=""
ISSUE_BODY=""

# 排除模式
EXCLUDE_PATTERNS=(
  "data/" "__pycache__/" "*.pyc" "config.json" "*.log" "*.xlsx" "*.csv"
)

# 自动发现技能：扫描 .user_skills/ 下所有带 SKILL.md 的目录
# 返回技能名称列表（每行一个）
discover_skills() {
  for dir in "$SKILLS_SRC"/*/; do
    if [ -f "$dir/SKILL.md" ]; then
      basename "$dir"
    fi
  done
}

# 解析参数
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=true; shift ;;
    --status) COMMAND="status"; shift ;;
    --promote) COMMAND="promote"; PROMOTE_SKILL="$2"; shift 2 ;;
    --issue) COMMAND="issue"; ISSUE_SKILL="$2"; ISSUE_TITLE="$3"; ISSUE_BODY="${4:-}"; shift 4 ;;
    *) echo "未知参数: $1"; exit 1 ;;
  esac
done

# 确保状态目录存在
mkdir -p "$(dirname "$STATUS_FILE")"

# 初始化状态文件
init_status() {
  if [ ! -f "$STATUS_FILE" ]; then
    echo '{}' > "$STATUS_FILE"
  fi
}

# 读取技能状态
get_status() {
  local skill="$1"
  local field="$2"
  python3 -c "
import json
with open('$STATUS_FILE') as f:
    d = json.load(f)
s = d.get('$skill', {})
print(s.get('$field', ''))
" 2>/dev/null || echo ""
}

# 更新技能状态
set_status() {
  local skill="$1"
  local field="$2"
  local value="$3"
  python3 -c "
import json
with open('$STATUS_FILE') as f:
    d = json.load(f)
if '$skill' not in d:
    d['$skill'] = {}
d['$skill']['$field'] = '$value'
with open('$STATUS_FILE', 'w') as f:
    json.dump(d, f, ensure_ascii=False, indent=2)
"
}

# 命令：查看状态
cmd_status() {
  init_status
  echo "=========================================="
  echo " F.LUX 技能验证状态"
  echo " 状态文件: $STATUS_FILE"
  echo "=========================================="
  echo ""
  for skill in $(discover_skills); do
    local branch=$(get_status "$skill" "current_branch")
    local days=$(get_status "$skill" "consecutive_success_days")
    local last_date=$(get_status "$skill" "last_run_date")
    local last_status=$(get_status "$skill" "last_run_status")
    local promoted=$(get_status "$skill" "promoted_to_main")

    echo "📦 $skill"
    echo "   当前分支: ${branch:-未推送}"
    echo "   连续无报错: ${days:-0} 天"
    echo "   最后执行: ${last_date:-无} (${last_status:-未知})"
    if [ -n "$promoted" ]; then
      echo "   ✅ 已合并到main: $promoted"
    else
      echo "   ⏳ 未合并到main（需连续3天无报错）"
    fi
    echo ""
  done
}

# 命令：提升到main（合并dev→main）
cmd_promote() {
  local skill="$PROMOTE_SKILL"
  if [ -z "$skill" ]; then
    echo "❌ 请指定技能名: --promote <技能名>"
    exit 1
  fi

  # 检查是否是有效技能目录（.user_skills/ 下有对应 SKILL.md）
  if [ ! -f "$SKILLS_SRC/$skill/SKILL.md" ]; then
    echo "❌ 技能 $skill 不存在（未在 $SKILLS_SRC/ 下找到 SKILL.md）"
    exit 1
  fi

  local days=$(get_status "$skill" "consecutive_success_days")
  days=${days:-0}

  if [ "$days" -lt 3 ]; then
    echo "⚠️  技能 $skill 连续无报错仅 $days 天，不足3天，不能合并到main"
    echo "   请继续验证，满3天后再执行提升"
    exit 1
  fi

  cd "$REPO_DIR"

  # 检查dev分支是否存在
  if ! git show-ref --quiet "refs/heads/dev/$skill"; then
    echo "❌ 分支 dev/$skill 不存在"
    exit 1
  fi

  echo "✅ 技能 $skill 已连续 $days 天无报错，准备合并到main"
  echo ""

  if [ "$DRY_RUN" = true ]; then
    echo "   [干跑] 将执行: git checkout main && git merge dev/$skill && git push origin main"
    return
  fi

  # 合并到main
  git checkout main
  git merge "dev/$skill" -m "promote: $skill 合并到main（连续${days}天无报错验证通过）"
  git push origin main

  # 更新状态
  set_status "$skill" "promoted_to_main" "$(date '+%Y-%m-%d %H:%M')"
  set_status "$skill" "current_branch" "main"

  echo ""
  echo "🎉 技能 $skill 已成功合并到main分支！"
}

# 命令：创建Issue
cmd_issue() {
  local skill="$ISSUE_SKILL"
  local title="$ISSUE_TITLE"
  local body="$ISSUE_BODY"

  if [ -z "$skill" ] || [ -z "$title" ]; then
    echo "❌ 用法: --issue <技能名> \"<标题>\" \"<内容>\""
    exit 1
  fi

  cd "$REPO_DIR"

  if [ "$DRY_RUN" = true ]; then
    echo "[干跑] 将创建Issue: [$skill] $title"
    echo "内容: $body"
    return
  fi

  # 用gh创建Issue
  local result
  result=$(gh issue create \
    --repo "lengzhiboqin/F.LUX-SKILL" \
    --title "[$skill] $title" \
    --body "$body" \
    --label "技能优化建议" 2>&1) || true

  if echo "$result" | grep -q "https://github.com"; then
    echo "✅ Issue已创建: $result"
  else
    echo "⚠️  Issue创建可能失败: $result"
    echo "   请检查gh认证状态"
  fi
}

# 命令：同步（默认）
cmd_sync() {
  init_status
  echo "=========================================="
  echo " F.LUX-SKILL 同步脚本（v3 自动发现+分支管理）"
  echo " 源目录: $SKILLS_SRC"
  echo " 仓库目录: $REPO_DIR"
  echo " 模式: $([ "$DRY_RUN" = true ] && echo "干跑" || echo "执行")"
  echo "=========================================="
  echo ""

  local any_changed=false
  local new_skills_found=()

  for skill in $(discover_skills); do
    local SRC="$SKILLS_SRC/$skill"
    local DST="$REPO_DIR/$skill"
    local branch="dev/$skill"

    if [ ! -d "$SRC" ]; then
      echo "⚠️  跳过 $skill: 源目录不存在"
      continue
    fi

    # 检测是否是新技能（仓库里还没有这个目录）
    local is_new=false
    if [ ! -d "$DST" ]; then
      is_new=true
      new_skills_found+=("$skill")
    fi

    echo "📦 检查技能: $skill → 分支 $branch $([ "$is_new" = true ] && echo "🆕 新技能")"

    mkdir -p "$DST"

    # rsync同步
    RSYNC_ARGS=(-a --delete)
    for pat in "${EXCLUDE_PATTERNS[@]}"; do
      RSYNC_ARGS+=(--exclude="$pat")
    done
    # 始终加 --itemize-changes，这样同步后能检测到变化（修复：真正运行时也能检测新文件）
    RSYNC_ARGS+=(--itemize-changes)
    if [ "$DRY_RUN" = true ]; then
      RSYNC_ARGS+=(--dry-run)
    fi

    local OUTPUT
    OUTPUT=$(rsync "${RSYNC_ARGS[@]}" "$SRC/" "$DST/" 2>&1) || true

    local has_change=false
    if [ -n "$OUTPUT" ]; then
      # 只看有实际文件变化的行（<f 或 >f 开头）
      if echo "$OUTPUT" | grep -qE "^[<>][fc]"; then
        has_change=true
        any_changed=true
      fi
    fi

    if [ "$has_change" = true ]; then
      echo "   🔄 检测到变化，推送到 $branch"
      echo "$OUTPUT" | grep -E "^[<>][fc]" | while IFS= read -r line; do
        echo "      $line"
      done

      if [ "$DRY_RUN" = false ]; then
        cd "$REPO_DIR"

        # 创建/切换到dev分支
        git checkout -B "$branch" 2>/dev/null || git checkout "$branch"

        # 提交并推送
        git add -A
        if ! git diff --cached --quiet; then
          git commit -m "sync: 更新 $skill ($(date '+%Y-%m-%d %H:%M'))"
          git push -u origin "$branch" 2>&1
          echo "   ✅ 已推送到 $branch"
        else
          echo "   无实际文件变化"
        fi

        # 切回main（保持工作区干净）
        git checkout main 2>/dev/null || true

        # 更新状态：技能有变化，重置连续无报错天数（需要重新验证）
        set_status "$skill" "current_branch" "$branch"
        set_status "$skill" "consecutive_success_days" "0"
        set_status "$skill" "promoted_to_main" ""
      fi
    else
      echo "   ✅ 无变化"
    fi
    echo ""
  done

  # 新技能汇总通知
  if [ ${#new_skills_found[@]} -gt 0 ]; then
    echo "🆕 发现新技能（已自动加入同步）："
    for ns in "${new_skills_found[@]}"; do
      echo "   - $ns"
    done
    echo ""
  fi

  if [ "$any_changed" = false ]; then
    echo "✅ 所有技能已是最新，无需同步"
  fi

  echo "=========================================="
  echo " 同步完成"
  echo " 提示: 查看验证状态用 bash sync_skills.sh --status"
  echo " 提示: 连续3天无报错后用 bash sync_skills.sh --promote <技能名>"
  echo "=========================================="
}

# 主入口
case "$COMMAND" in
  status) cmd_status ;;
  promote) cmd_promote ;;
  issue) cmd_issue ;;
  sync) cmd_sync ;;
esac
