#!/usr/bin/env bash
# F.LUX-SKILL 正向部署脚本（git → 生产 .user_skills）
#
# 解决的问题：云电脑容器无状态，容器重建后 /runtime 从持久镜像 restore，
#   可能是旧代码；定时任务会话开机先跑本脚本，把 git 上的最新代码覆盖到生产目录，
#   再采集，保证跑的是新版、且不会被反向 sync 污染。
#
# 部署源默认是【远程】origin/dev/<技能名>（先 fetch），不依赖本地分支是否被污染。
#
# 用法:
#   bash deploy_skills.sh                          # 部署全部技能（各自 origin/dev/<技能>）
#   bash deploy_skills.sh --skill flux-daily-reports
#   bash deploy_skills.sh --skill flux-daily-reports --ref origin/main
#   bash deploy_skills.sh --skip-fetch             # 跳过 fetch（离线/网络不通时）
#   bash deploy_skills.sh --dry-run
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILLS_DST="/home/user/.doubao/agent_mode/workspace/.user_skills"

DRY_RUN=false
ONLY_SKILL=""
OVERRIDE_REF=""
SKIP_FETCH=false

# 部署到生产时必须保留/排除的内容（本地数据、凭据、下载原件、缓存）
EXCLUDE_PATTERNS=(
  "data/"
  "__pycache__/"
  "*.pyc"
  "config.json"
  "*.log"
  "_data/"
  "*.db"
  "*.db-wal"
  "*.db-shm"
  "output/"
  "*.bak"
  "牵牛花已下载表_v2/"
  "ad_raw/"
  "store_raw/"
)

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)    DRY_RUN=true; shift ;;
    --skill)      ONLY_SKILL="$2"; shift 2 ;;
    --ref)        OVERRIDE_REF="$2"; shift 2 ;;
    --skip-fetch) SKIP_FETCH=true; shift ;;
    *) echo "未知参数: $1"; exit 1 ;;
  esac
done

# 列出 git 仓库中所有含 SKILL.md 的技能目录
list_skills() {
  cd "$REPO_DIR"
  for d in */; do
    skill="${d%/}"
    if [ -f "$skill/SKILL.md" ]; then
      echo "$skill"
    fi
  done
}

deploy_one() {
  local skill="$1"
  local ref="${OVERRIDE_REF:-origin/dev/$skill}"

  echo "📦 部署 $skill  ←  $ref"

  cd "$REPO_DIR"
  if ! git rev-parse --verify "$ref" >/dev/null 2>&1; then
    echo "  ❌ git 引用不存在: $ref"
    return 1
  fi

  # 把该技能从指定 git 引用导出到临时区
  local stage
  stage=$(mktemp -d)
  trap 'rm -rf "${stage:-}"' EXIT
  if ! git archive "$ref" "$skill/" 2>/dev/null | tar -x -C "$stage"; then
    echo "  ❌ 导出失败：$ref 中是否有 $skill/ 目录？"
    return 1
  fi

  if [ ! -d "$stage/$skill" ]; then
    echo "  ❌ 导出内容不含 $skill/"
    return 1
  fi

  # 同步到生产目录（--delete 保证删除旧文件；排除项保护本地数据/凭据）
  local rsync_args=(-a --delete)
  for p in "${EXCLUDE_PATTERNS[@]}"; do
    rsync_args+=(--exclude="$p")
  done
  if [ "$DRY_RUN" = true ]; then
    rsync_args+=(--dry-run)
  fi

  mkdir -p "$SKILLS_DST/$skill"
  local out
  out=$(rsync "${rsync_args[@]}" "$stage/$skill/" "$SKILLS_DST/$skill/" 2>&1)

  if [ -n "$out" ]; then
    echo "$out" | grep -E '^[<>c\.\*]' | sed 's/^/    /' | head -20
  fi
  echo "  ✅ $skill 已部署$([ "$DRY_RUN" = true ] && echo '（干跑）')"
}

cd "$REPO_DIR"
if [ "$SKIP_FETCH" = false ]; then
  echo "== 拉取远程最新（fetch） =="
  if git fetch origin --prune 2>&1 | tail -2; then :; fi
  echo ""
fi

fail=0
if [ -n "$ONLY_SKILL" ]; then
  deploy_one "$ONLY_SKILL" || fail=1
else
  for skill in $(list_skills); do
    deploy_one "$skill" || fail=1
    echo ""
  done
fi

if [ "$fail" -ne 0 ]; then
  echo "⚠️ 部分技能部署失败"
  exit 1
fi
echo "== 部署完成 =="
