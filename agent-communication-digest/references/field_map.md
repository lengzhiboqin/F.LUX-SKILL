# 多维表格字段规范（智能体沟通记录归纳）

Base「智能体沟通记录归纳」`MLMUbhhkrahWQSsHIg5cT2KfnNW`，表「沟通记录」`tblDo2je1NsZoEn5`。

## 字段清单（14 个）

| 字段名 | 字段ID | 类型 | 写值要求 |
|---|---|---|---|
| 日期 | fldv23tabf | datetime | `YYYY-MM-DD`（入表用 `YYYY-MM-DD HH:mm:ss` 更稳） |
| 知识类型 | fldbDeicjf | 多选 | FACT / DECISION / EXPERIENCE / INSIGHT / AI_HYPOTHESIS |
| 分类 | fldc7TuPlx | 单选 | 观点表达 / 业务理解 / 重大决策 / 个人偏好 / 生活经历 / AI推断待确认 |
| 关联md | fldg0zrywf | text | md 文件相对路径或文件名 |
| AI归纳 | fldGmH9Jgi | text | AI 归纳文字 |
| 原话引用 | fldLH7yrT2 | text | 用户原话（真实原话时必填） |
| Agent | fldLUvUJda | 单选 | 豆包 / Hermes / Codex / Claude / WorkBuddy |
| 会话窗口 | fldmq0xEFN | text | 窗口名（如「运营牛马」「拯救屎山项目」） |
| 口径标注 | fldNjzns1G | 单选 | 真实原话 / summary转述 / 工作日志转述 |
| 条目标题 | fldPot2nrZ | text | 去重键之一（与日期组合） |
| 来源 | fldQFXN5Qq | text | 源文件路径 + 时间戳 |
| 标签 | fldT545AHH | 多选 | 自由标签，写前确认选项池（现 278 选项） |
| 备注 | fldV5f4idQ | text | 补充说明（如【待核实】） |
| 确认状态 | fldVg5ydgE | 单选 | 待确认 / 已确认 / 已修正 |

## 选项池纪律

- select/多选字段写入**未知选项**会触发平台报错（800030005）或静默新增。
- 写前用 `lark-cli base +field-search-options --field-id <id> --limit 300` 拉全量选项。
- 需要新增选项时用 `+field-update` **全量 PUT**（必须带旧选项），执行后回读。
- 标签池已扩到 278 个；后续如需收敛由用户拍板，禁止 AI 擅自删选项。

## 去重规则（Hermes 双写事故教训）

- 去重键 = 条目标题 + 日期（前 10 位）。
- 入表前先 `+record-list` 拉全量，比对后跳过已存在条目。

## 分类口径

| 分类 | 何时用 |
|---|---|
| 观点表达 | 你如何看待/评价某事 |
| 业务理解 | 你对机制/流程的认知 |
| 重大决策 | 你拍板的事项（标【关键决策】） |
| 个人偏好 | 工作/沟通习惯 |
| 生活经历 | 与业务无关的个人生活 |
| AI推断待确认 | AI 归纳推断，待你确认（表格化呈现） |
