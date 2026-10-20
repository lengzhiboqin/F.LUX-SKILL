---
name: flux-skill-validator
description: "F.LUX技能质量门禁与GitHub同步技能。每日自动检测所有用户技能的执行状态，更新连续无报错验证天数，报错时自动创建GitHub Issue，代码变化自动推送到dev验证分支，连续3天无报错提醒合并到main主线。用于：每日技能复盘、技能质量门禁、GitHub自动同步、新技能自动发现、dev/main分支管理、技能执行状态监控。也适用于手动查看技能验证状态、手动触发同步、手动提升技能到main。"
---

# F.LUX 技能质量门禁与GitHub同步

**一键闭环**：自动发现所有技能 → 检查当日执行状态 → 更新验证天数 → 报错建Issue → 同步代码到dev分支 → 检查可合并main → 输出结构化结果。

## 快速开始

### 每日自动检测（07复盘调用）

```bash
python3 <技能目录>/scripts/validate_skills.py \
  --skills-dir /home/user/.doubao/agent_mode/workspace/.user_skills \
  --repo /home/user/Doubao/chats/38439034639855618/F.LUX-SKILL \
  --status-file ~/.config/flux-skills/skill_status.json \
  --date 20260918 \
  --sync \
  --notify
```

### 只查看状态（不同步）

```bash
python3 <技能目录>/scripts/validate_skills.py --status-only
```

### 手动提升技能到main（需连续3天无报错）

```bash
bash <repo>/sync_skills.sh --promote <技能名>
```

## 核心功能

### 1. 自动发现技能

扫描 `--skills-dir` 下所有带 `SKILL.md` 的目录，自动纳入检测范围（取消白名单）。

### 2. 执行状态检测

逐个检查技能当日执行状态：

| 技能 | 检测方式 | 成功条件 |
|---|---|---|
| shangou-data-pipeline | 读 `data/master/last_pipeline.json` | exit_code=0 且 总表8张表行数>0 |
| flux-daily-briefing | 检查输出目录是否有当日报简文件 | `F.LUX每日经营简报_YYYYMMDD_v2_full.md` 存在 |
| 其他技能 | 检查技能目录下是否有当日执行日志或产出物 | 有当日产出物或日志标记为success |

无法自动检测的技能标记为 `unknown`，不影响验证天数（需人工确认）。

### 3. 质量门禁（连续3天无报错）

- 当日执行成功 → `consecutive_success_days` +1
- 当日执行失败 → `consecutive_success_days` 重置为0，记录报错信息
- 连续3天无报错 → 提醒可合并到main
- 代码有变化推送到dev → 重置验证天数（需要重新验证）

### 4. GitHub同步

调用仓库中的 `sync_skills.sh` 完成：
- 对比本地技能与仓库版本（排除data/、__pycache__/、config.json）
- 有变化 → 推送到 `dev/<技能名>` 分支
- 新技能 → 自动加入同步，推送到dev分支
- 同步失败不阻塞主流程（记录错误继续）

### 5. 报错建Issue

技能执行失败时，自动创建GitHub Issue：
- 标题：`[技能名] 报错摘要`
- 标签：`技能优化建议`
- 内容：报错详情、复现步骤、影响范围
- 不自动修改代码，等用户确认后才修复

## 输出格式

执行完成后输出结构化JSON（stdout）：

```json
{
  "date": "2026-09-18",
  "total_skills": 4,
  "success": 2,
  "failed": 0,
  "unknown": 2,
  "new_skills": ["xhs-publisher"],
  "synced_to_dev": ["shangou-data-pipeline"],
  "ready_for_main": [],
  "issues_created": [],
  "skills": [
    {
      "name": "shangou-data-pipeline",
      "status": "success",
      "consecutive_days": 4,
      "branch": "main",
      "promoted": true
    }
  ]
}
```

## 命令行参数

| 参数 | 说明 | 默认值 |
|---|---|---|
| `--skills-dir` | 技能源目录 | `~/.doubao/agent_mode/workspace/.user_skills` |
| `--repo` | GitHub仓库本地路径 | 自动检测 |
| `--status-file` | 技能状态文件路径 | `~/.config/flux-skills/skill_status.json` |
| `--date` | 数据日期（YYYYMMDD） | 昨日自动计算 |
| `--sync` | 执行GitHub同步 | false（只检测不同步） |
| `--notify` | 发送企微通知 | false |
| `--status-only` | 只查看状态，不更新 | false |
| `--dry-run` | 干跑模式（不修改任何文件） | false |

## 与调度器的集成

07每日复盘任务中调用本技能：

```bash
# 1. 执行技能检测+GitHub同步
python3 <技能目录>/scripts/validate_skills.py --sync --notify

# 2. 读取输出JSON，提取结果用于复盘报告和企微通知
```

## 状态文件结构

`~/.config/flux-skills/skill_status.json`：

```json
{
  "shangou-data-pipeline": {
    "current_branch": "main",
    "consecutive_success_days": 3,
    "last_run_date": "2026-09-17",
    "last_run_status": "success",
    "last_error": null,
    "promoted_to_main": "2026-09-17 14:43"
  }
}
```

## 注意事项

1. **同步失败不阻塞**：GitHub同步或Issue创建失败时，记录错误但继续执行
2. **不自动修改技能代码**：报错时只创建Issue，用户确认后才修复
3. **不自动合并到main**：连续3天无报错只提醒用户，合并需要用户在GitHub审查后手动点Merge
4. **新技能自动加入**：取消白名单，所有带SKILL.md的技能目录都会被自动扫描和同步
5. **代码变化重置验证**：技能有代码变化推送到dev后，连续无报错天数重置为0，需要重新验证
