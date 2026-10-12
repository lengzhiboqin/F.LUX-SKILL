---
name: agent-communication-digest
description: 智能体沟通记录归纳管道。用于把本机所有 AI（豆包/Codex/Claude/WorkBuddy/Hermes）的沟通记录沉淀为「本地按 Agent 分文件夹+每日一 md（给 AI 读）」+「飞书多维表格「智能体沟通记录归纳」（给人读、可溯源）」。触发场景：跑一遍归纳 / 归纳昨天的沟通 / 各 agent 沟通记录汇总 / 智能体沟通记录入表 / 今日沟通速记 / 智能体沟通记录归纳。流程：scan 扫描数据源 → extract 提取用户原话 → digest 归纳 md → sync 增量入表 → verify 回读验证 → report 汇报。
license: 内部交付物，随「智能体沟通记录归纳」项目生效；未获授权不得外传。
---

# 智能体沟通记录归纳管道

把**每个 Agent 窗口里你输出的重要原话、观点、决策、偏好**变成两层产物：
- **本地 md**（给 AI 读）：`D:\个人档案\智能体沟通记录归纳\{Agent}\YYYY-MM-DD.md`
- **飞书多维表格**（给人读、可溯源）：「智能体沟通记录归纳」Base

> **核心原则（用户多轮确认，写死）**：
> 1. 数据真实禁止编造；多源交叉验证；不可核实标【待核实】。
> 2. 无真实沉淀不建占位 md（无正文、无沉淀的日期不建空文件）。
> 3. 每次做完归纳后**自动追加更新**到多维表格，不手工提醒。
> 4. 分类 6 类、知识类型 5 类、口径标注 3 类，字段值必须在选项池内（写表前先查选项）。
> 5. 入表按「条目标题+日期」去重（Hermes 曾发生批量双写事故，教训固定）。
> 6. 汇报称呼用户「老大」，末尾加「老大速读」只列 待执行/待确认/注意事项。

## 一、数据源清单（config.json 有完整路径）

| Agent | 数据源 | 提取要点 |
|---|---|---|
| 豆包 | 飞书云空间会话导出（运营牛马等窗口按会话分文件夹、按日 `_YYYYMMDD.md/jsonl`）；本地 `D:\个人档案\知识库` 沉淀 | 会话文件直接读，知识库沉淀按「知识库沉淀」口径入表 |
| Codex | `C:\Users\Administrator\.codex\sessions\2026\*\*\*\rollout-*.jsonl` | 新版用户消息在 `response_item:message` + `role:'user'`；过滤系统注入标签 |
| Claude | `C:\Users\Administrator\.claude\projects\*\*.jsonl` | 用户消息在 `type:'user'` 的 message；过滤系统注入 |
| WorkBuddy | `C:\Users\Administrator\.workbuddy\projects\*\*.jsonl` + `audit-log\*.jsonl` + `memory\*_memory.md` | 无逐字正文的日期用 summary/工作日志转述口径；10-05~08 类云端会话以知识库沉淀入表并说明 |
| Hermes | Hermes 会话存储（按实际路径配置） | 同上通用提取规则 |

## 二、运行流程（按用户意图选模块，不要一次跑全套）

| 用户说 | 做什么 | 执行 |
|---|---|---|
| 「看看各 agent 有什么新的」 | 只扫数据源找新记录 | `scan` |
| 「提取昨天的原话」 | 提取指定 Agent/日期用户原话候选 | `extract --agent X --date D` |
| 「归纳一下」 | 对提取结果写成每日 md（套模板） | `digest`（AI 主导） |
| 「入表 / 同步到多维表格」 | 增量写入多维表格 | `sync` |
| 「检查表里对不对」 | 回读验证 + 去重检查 | `verify` |
| 「今天整个流程跑一遍」 | 全流程 | `run` |

### 2.1 scan — 扫描新数据
- 按 config.json 的 data_sources 逐个扫描，对比 `D:\个人档案\智能体沟通记录归纳\{Agent}\` 已有 md 日期
- 输出：各 Agent 有哪些日期有数据但未归纳

### 2.2 extract — 提取用户原话
- 运行 `scripts/extract_agents.py --agent <name> [--date YYYY-MM-DD]`
- 输出按天分组的用户消息候选（过滤系统注入、environment_context、权限确认等噪音）
- 每条候选标注：时间戳、来源文件、口径（真实原话/summary转述/工作日志转述）

### 2.3 digest — 归纳成每日 md（AI 主导）
- 套用 `references/md_template.md` 的 7 板块模板
- 分类 6 类：观点表达 / 业务理解 / 重大决策 / 个人偏好 / 生活经历 / AI推断待确认
- 知识类型 5 类：FACT / DECISION / EXPERIENCE / INSIGHT / AI_HYPOTHESIS
- 每条含：来源、原话引用、子类型、AI归纳、标签
- 无真实沉淀的日期不建 md
- **信息量门槛（用户拍板，写死）**：无实质信息量的纯推进/确认词（继续、可以、1、OK、推进、好了、收到、行等）**不单独入库**。仅当其承载了可复用信息——决策、授权信号、新原则、明确要求（如「你决定」「1.坚持飞书」「消息排版做好换行」）——才保留。判断标准：去掉这句话，业务经验是否受损；不受损就不入表。

### 2.4 sync — 增量入表（确定性脚本）
运行 `scripts/sync_base.py`：
1. 读入表 JSON（AI 按字段规范构建）
2. 检查 select/多选字段选项池（标签、分类、知识类型、口径标注、Agent、确认状态），缺失选项自动补（`+field-search-options` 拉全量 → `+field-update` 追加，**必须带全量旧选项**）
3. 按「条目标题+日期」去重（先 `+record-list` 拉已有，避免双写）
4. 批量写入 `+record-batch-create`（单批 ≤200）
5. 回读验证行数与分布，输出验证报告

### 2.5 verify — 回读验证
- 拉全表按 Agent/日期/确认状态统计，与预期核对
- 重点：确认无重复条目（按条目标题去重）

### 2.6 report — 汇报
- 企微通知「老大」：本次入表 X 条、共多少条、异常项
- 末尾加「老大速读」

## 三、多维表格坐标（config.json 同步维护）

- Base「智能体沟通记录归纳」`MLMUbhhkrahWQSsHIg5cT2KfnNW`（位于「任务推进看板」文件夹）
- 表「沟通记录」`tblDo2je1NsZoEn5`
- 14 字段：日期/知识类型/分类/关联md/AI归纳/原话引用/Agent/会话窗口/口径标注/条目标题/来源/标签/备注/确认状态
- 字段 ID 与选项池见 `references/field_map.md`

## 四、硬约束

1. **写表前先确认选项存在**：select/多选字段写入未知选项会触发平台报错（800030005）或静默新增；全量选项用 `+field-search-options`（不是 `+field-get`，后者截断 50）。
2. **批量写入 ≤200 条**，连续写串行。
3. **每次归纳完必须入表**，不允许只写 md 不入表。
4. **Hermes 教训**：入表前必须拉已有记录按条目标题+日期去重。
5. **PowerShell 是唯一宿主通道**（Windows 主机无 Bash）；复杂 JSON 用 @file 传参；命令输出被吞用 `> 文件 2>&1` 重定向。
6. **lark-cli base 写命令前**：先读 `lark-base` 技能 SKILL.md 与对应 reference（record-batch-create / cell-value / field-update），禁止凭记忆调用。
7. **数据纪律**：只写真实观察；无法核实标【待核实】；不编造原话。
