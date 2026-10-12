---
name: flux-daily-reports
description: This skill should be used when the user asks to collect, download, refresh, clean, load, self-check, recover, or report the F.LUX daily operating data of 牵牛花 (Qianniu) and 闪购 (Shangou) merchant backends — 19 report tables on Windows and Linux hosts. It triggers on requests such as 采昨天 / 补某天 / 拉取今天的两平台报表 / 处理这个批次 / 把这个批次清洗入库 / 检查某段数据对不对 / 看某店某个指标 / 今天的数据跑完了吗 / 跑每日闪购数据. It plans a seven-day gap, collects both platforms, validates raw original files, cleans them by fixed rules, writes incrementally into one SQLite business database with a 365-day retention window, runs an independent self-check, and reports success or blockers honestly.
license: 内部交付物，随项目 spec v2 生效；未获授权不得外传。
---

# F.LUX 日常采集与入库技能包

一套**确定性的四阶段日常管线**：**查缺口 → 采集并验收原件 → 清洗入库 → 自检与汇报**。
确定性工作交给 `scripts/`，判断与用户沟通交给本文件。

> **版本 v0.2（2026-10-02）** —— 实机三轮验收通过后的交付版。
> v0.2 相对 v0.1 的实质变化：
> 1. **闪购支持定向日期回采**：新增日期控件设置与「日期校验双闸门」，可精确回采指定历史日
>    （gen2 两张流量表仍受限，见下）。
> 2. **19 张事实表全部入库**：`门店流量_分来源` / `门店流量排行` 按 2026-10-02 裁决恢复入库。
> 3. **修复 5 项实机缺陷**：`ws.sheetnames`、`QIANNIU_ID_KEYS` 大小写、`optional_keys` 透传、
>    事实表 `f_` 前缀、`_catalog` 漏登记。
> 4. **云端无桌面宿主可用**：新增 `cloud_vnc_daemon.sh`（Xvfb + x11vnc + noVNC + CDP 全套）。
> 5. **取数说明书重生成**：578 列，可用指标 290 → 383，与库双向零差异。

## 一、先读懂这三条不变量

1. **单一批次身份贯穿全程。** 规划产生 `batch_id`；采集、清洗、入库、自检、汇报必须
   沿用同一个 `batch_id`。交接**靠批次清单**，不靠某个固定目录里的同名文件。
2. **全批次闸门。** 本批次**全部**任务都 `validated` 或 `no_data`，才允许清洗入库。
   任一任务 `blocked/failed`，**整批拒绝**，已采原件留存供恢复，旧成功库继续可查。
3. **只读操作绝不写库。** 自检与查询使用 `mode=ro` + `query_only`；发现已提交数据有异常时
   **只报告、不回改**，按显式修正批次处理。

## 二、按用户意图选模块（不要一次跑全套）

| 用户说 | 做什么 | 命令 |
|---|---|---|
| 「看看还缺哪些」「今天要采什么」 | 只查七日缺口，不采集 | `check-gap` |
| 「采昨天」「拉今天的两平台报表」 | 默认日期全量采集（19 表） | `collect` |
| 「补 9 月 20 号」 | 定向日期重采，**新建批次** | `collect --date` |
| 「只采评价数据」 | 限定单表采集 | `collect --table` |
| 「把这个批次清洗好」 | 只清洗不写库 | `process`（**不带** `--db`） |
| 「处理这个批次」「入库」 | 清洗 + 单事务入库 | `process --db` |
| 「检查这段数据对不对」 | 独立自检 | `selfcheck` |
| 「这个批次卡住了，接着跑」 | 只重跑受阻任务，沿用原批次 | `resume` |
| 「这批数据到底成不成」 | 生成成功/未完成报告 | `report` |
| 「清一下过期数据」 | 保留期维护（先备份） | `retain` |
| 「今天整个流程跑一遍」 | 缺口→采集→入库→自检→汇报 | `run` |

**独立调用边界（必须守住）**

- 采集模块**不清洗、不写业务库**。
- 清洗入库模块**不打开平台、不自动补采**，只消费清单里列明的原件。
- 自检/查询**只读**。
- 只读查询用 `scripts/query.mjs`（Node），不进 Python 主链。

## 三、标准日常流程

```bash
# 0) 一次准备：把 config.example.json 复制成 config.json，按宿主改路径
# 1) 看缺口（可跳过，run 会自己做）
python scripts/flux.py check-gap  --today 2026-10-01 --config config.json
# 2) 采集（默认 19 表；台账里已完成的目标日任务会自动跳过）
python scripts/flux.py collect    --today 2026-10-01 --config config.json
# 3) 清洗入库（--manifest 用上一步输出里的 manifest 路径）
python scripts/flux.py process    --manifest _data/_batches/<batch_id>/manifest.json \
                                  --rules references/cleaning_rules.json \
                                  --master _data/store_master.json \
                                  --db _data/flux.db
# 4) 自检（只读）
python scripts/flux.py selfcheck  --db _data/flux.db --mode sample --sample-size 200
# 5) 汇报
python scripts/flux.py report     --manifest _data/_batches/<batch_id>/manifest.json
```

需要**一条命令跑完全程**时用 `run`（任一步失败即停，并输出【未完成】模板）。

**要采集必须开浏览器**：牵牛花用已登录浏览器的 CDP 端口（默认 `127.0.0.1:9222`），
闪购用 Playwright 连接同一个 CDP。登录失效、验证码、扫码都要**请用户本人完成**，
再 `resume` 从受阻任务继续。绝不输出凭据、绝不自动清 Cookie。

## 四、日期口径（写死，不要猜）

| 项目 | 口径 |
|---|---|
| 业务日 T | 运行开始时的 `Asia/Shanghai` 日期，跨午夜不变；非法日历日期直接报错 |
| 普通日表 | 目标 **T-1**；最近窗口 **T-7 ~ T-1**（含两端 7 天） |
| 闪购评价数据 | 目标 **T-2**；最近窗口 **T-8 ~ T-2**（含两端 7 天） |
| 商品流量_近30天 | 快照，覆盖 **T-30 ~ T-1**，**以 T-1 为窗口结束日**，不裁切、不倒退 |
| 保留期 | 活动库保留业务日期 **T-365 ~ T-1**（含两端 365 个自然日），按**业务日期**判期 |

历史快照只有平台**实际支持回溯**时才补采；不支持就明确列为限制，**不得从当前快照伪造**。

## 五、失败与恢复（最多 3 次，不无限重试）

1. 先看错误码：`LOGIN_REQUIRED` / `BROWSER_UNAVAILABLE` / `STRUCTURE_CHANGED` /
   `DATE_MISMATCH` / `SCOPE_MISMATCH` / `EMPTY_DOWNLOAD` / `ZERO_BYTES` /
   `PARSE_FAILED` / `TASK_NOT_FOUND` / `TIMEOUT` / `PLATFORM_ERROR` / `NO_DATA_UNVERIFIED`。
2. **能自动恢复的**：网络抖动、页面未加载、布局小变化（唯一同义定位且结构一致）→
   有界重试（默认最多追加 2 次，即总计 3 次），每次记录尝试。
3. **必须停下来找人的**：登录失效/验证码、表头或列变化、单位/口径/语义变化、
   日期或范围不符。**不自动补列、不改名、不猜口径。**
4. 恢复沿用**原业务身份与原批次**；只有用户要求重采历史时才新建批次。
5. 详见 `references/recovery.md`。

## 六、几个必须守住的业务细节

- **修订是整组替换**：源端某组减少或变为无事件时，按「表 + 业务日期集合 + 范围」整组覆盖，
  避免残留幽灵行导致销量/金额多计。
- **无事件不是没采到**：必须附平台证据（导出任务 ID / 无事件响应 / 截图）才算完成；
  0 字节、解析失败、缺行都**不等于**无事件。
- **两平台同口径不能重复相加**；全店汇总行不摊到门店。
- **门店未匹配就留空**，标待核，**不猜 ID**。
- **待核指标不输出正式值**；长编号保真（不进浮点），空值不填 0，百分比与百分点不混用。

## 七、取数与问答（用户不直接看库，由 AI 代取）

**用户全程不看数据库**；他用自然语言或定时任务提示词提问，由 AI 取数并回报。取数**必须**按下面三步走，不允许「凭表名猜列名」直接写 SQL。

### 第一步：读说明书定位

先读 `references/metrics.json` 找到目标表与列。它的结构：

| 字段 | 含义 |
|---|---|
| `tables[]` | 19 张表清单，含 `in_db`、`db_columns`（库内真实列名）、各类指标计数 |
| `columns[]` | 566 列的逐列口径：`table`/`column`/`unit`/`grain`/`meaning`/`window`/`caution`/`availability` |
| `not_loaded_metrics[]` | 125 个**未入库**口径（库里没有这些列，别写进 SQL） |

**`availability` 决定能不能取**：

| 取值 | 含义 | 取数动作 |
|---|---|---|
| `usable` | 290 个正式指标 | **可以取**，写进 `SELECT` |
| `dimension` | 111 个查询维度 | 只能 `GROUP BY` / `WHERE` |
| `attribute` | 40 个标识/文本属性 | 可展示、可筛，**不可求和** |
| `not_loaded` | 125 个未入库 | **库里没有，别写** |
| `pending` | 口径未决 | **先问用户**，不许擅自取 |

`caution` 非空 = 该列有陷阱（如「含退款」「仅全店汇总」「口径与他表不同」），**务必读**。

### 第二步：只读查询

用 `scripts/query.mjs`（Node ≥ 24，内置 `node:sqlite`，零第三方依赖），**只读**打开库
（`readOnly` + `query_only`，绝不写库）。它是**子命令**式，不是任意 SQL：

| 子命令 | 用途 | 主要参数 |
|---|---|---|
| `catalog` | 查库内真实列字典 | `--table` |
| `status` | 查覆盖与最近批次、最新业务日期 | `--table` |
| `stores` | 门店主档 | `--id` |
| `get` | **取值** | `--table --date --metric --store --scope --limit` |
| `lineage` | 反查原件来源 | `--table --date` |
| `audit` | 库级审计（批次、覆盖率、指纹） | — |

```bash
# 先看这表有哪些列、最新到哪天
node scripts/query.mjs status --db "_data/flux.db" --table f_门店经营指标
# 取某天某店的明细
node scripts/query.mjs get --db "_data/flux.db" \
  --table f_门店经营指标 --date 2026-09-30 --store 1203989 --limit 50
```

`get` 返回**行明细**（不做聚合）。需要「汇总/分组/跨天趋势」时，**不要在 `get` 上硬凑**：
用 Node 内置 `node:sqlite` 自己开只读连接跑分组 SQL，或先 `get` 出明细再在内存聚合。
无论哪种方式，SQL 里都要遵守下面三条纪律。

**三条取数纪律**：

1. **必须排除汇总行**：`_是否汇总行='1'` 的行是全店/全品类汇总，混进明细会双计。
2. **两平台同口径不可相加**：牵牛花与闪购是不同渠道，跨平台汇总要先确认口径（见列内 `cross_platform`）。
3. **空值不填 0**：库里空 = 该指标未采用或未采集，不等于 0；分组统计要显式说明空值处理。

### 第三步：回报

给出「口径 + 数据 + 结论」三段式：先一句话讲清用的哪张表哪些列什么区间，再给表格，最后给结论。**指标口径不确定就直说，不要编。**

### 常见提问 → 取数映射

| 用户说 | 取哪张表 | 关键列 |
|---|---|---|
| 「昨天卖了多少」 | `f_门店经营指标`（分店）/ `f_品类分析`（全店） | `有效订单金额` / `总销售收入` |
| 「哪家店最好/最差」 | `f_门店经营指标` | `有效订单金额` `客单价`，按 `门店` 分组 |
| 「哪些商品好卖」 | `f_商品数据` | `销量` `销售额`，按 `商品` 分组 |
| 「评价怎么样」 | `f_评价数据` | `商家评分` `订单商品点踩率(%)` |
| 「配送慢不慢」 | `f_配送指标明细` | `配送时长` 等时长列 |
| 「跟上周比」 | 同上任一张 | 同口径换日期区间，**不要跨平台** |

## 八、references 索引（按需加载，不要全读）

| 文件 | 什么时候读 |
|---|---|
| `references/installation.md` | 在新宿主安装、装依赖、配浏览器与 Node 版本 |
| `references/collection.md` | 采集口径、原件验收规则、结构契约、台账 |
| `references/processing.md` | 清洗规则、入库语义、自检与 365 日保留 |
| `references/recovery.md` | 错误码→处置、重试上限、变化分级、汇报模板与查询 |
| `references/metrics.json` | **AI 取数前必读**：19 表 578 列的取数说明书（口径、可用性、陷阱，与库实测零差异） |
| `references/schema.md` | 最终库字段字典与表关系 |
| `references/table_registry.json` | 19 表清单、平台、类型、窗口、列数基线 |
| `references/cleaning_rules.json` | 逐表清洗与入库规则（版本化，唯一规则来源） |
| `references/historical_headers.json` | 历史有序表头基线 |
| `references/config.example.json` | 配置样例（无凭据、无本机绝对路径） |
| `references/store_master.example.json` | 门店主档**样例**；真实主档放运行目录 `_data/store_master.json`，**不进包** |

> 包里只有 `.example.*`；凡带凭据/本机路径/业务数据的真实文件一律在运行目录，不进包。

## 九、给后续 agent 的硬性提醒

- 不要在别处**再写一份硬编码表名**；表清单只认 `table_registry.json`。
- 不要为了「让流程继续跑」而放宽任何验收；宁可真报【未完成】。
- 报告只报**实际完成的环节**：只采集成功时**不许**写「已入库」。
- 真实账号、Cookie、原件、门店主档**不进技能包**，留在运行工作目录。
