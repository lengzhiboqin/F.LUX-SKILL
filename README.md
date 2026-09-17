# F.LUX-SKILL

F.LUX 品牌运营自动化技能包。将日常工作流拆解为可复用的 AI 技能，通过调度器串行调度执行。

## 技能清单（中文简介）

### 1. shangou-data-pipeline — 美团闪购数据采集管线

**一句话说明：** 每天自动登录美团闪购商家后台，把8张经营报表下载下来，清洗合并成一张总表，同步到飞书云空间。

**解决什么问题：**
- 以前每天要手动登录商家后台，一张一张下载8个CSV报表，再手动整理Excel，耗时30-60分钟
- 现在全自动：登录态保持 → 自动下载8表 → 自动清洗去重 → 自动累积成历史总表 → 自动导出Excel → 自动同步飞书

**8张报表包括：** 门店财务明细、门店成交明细、商品数据、问题订单数据、流量明细、流量渠道明细、评价数据、售后订单数据

**核心能力：**
- 浏览器CDP自动采集（模拟人工操作，不依赖API）
- 幂等追加（同一天重跑不会重复数据）
- 缺失数据检测（自动发现哪天空了）
- 单表补采（某张表失败可以单独重采）
- 飞书云空间自动同步（同名覆盖）

**适用场景：** 每日定时采集、历史数据补采、数据缺失回补、新环境初始化

---

### 2. flux-daily-briefing — F.LUX每日经营简报生成

**一句话说明：** 读取上面采集技能产出的总表，自动分析品牌经营数据，生成一份11章节的详细经营简报，上传飞书并推送企业微信摘要。

**解决什么问题：**
- 以前要手动从Excel里拉数据、算环比、排门店排名、找异常，再写简报，耗时30分钟
- 现在全自动：读取总表 → 多维度分析 → 生成结构化简报 → 上传飞书 → 企微推送核心数据

**11章节包括：** 品牌整体经营数据、环比变化、门店营业额TOP10、门店净收入TOP10、商品销售TOP10、流量分析（漏斗转化）、流量渠道分布、评价与售后、异常门店识别、关键发现与运营建议、辅助资料使用声明

**核心能力：**
- 净收入口径校准（收入-佣金-配送费-商家补贴-公益捐款-其他费用）
- 环比自动对比（昨日 vs 前日）
- 异常门店自动识别（零订单、高取消率）
- 企微通知一段话摘要（营业额/环比/订单/客单价/活跃门店/TOP3/异常）

**适用场景：** 每日定时生成经营简报、临时数据分析、经营数据汇报

---

## 仓库文件说明

| 文件 | 作用 |
|---|---|
| `README.md` | 本文件，技能包说明文档 |
| `.gitignore` | Git忽略规则：告诉git哪些文件不要提交到仓库（见下方详解） |
| `sync_skills.sh` | 同步脚本：把本地技能源码自动同步到本仓库并推送到GitHub（见下方详解） |
| `shangou-data-pipeline/` | 技能1：美团闪购数据采集管线 |
| `flux-daily-briefing/` | 技能2：每日经营简报生成 |

### .gitignore 是什么？

`.gitignore` 是Git的"排除清单"。你在里面写了哪些文件/文件夹，Git提交时就会自动跳过它们，不会上传到GitHub仓库。

**本仓库的 .gitignore 排除了：**
- `data/` — 业务数据目录（45MB，含总表Excel、CSV累积表、原始报表），数据太大且含业务信息，不适合放GitHub
- `__pycache__/`、`*.pyc` — Python运行时自动生成的缓存文件，没用
- `config.json` — 配置文件（含本地路径、飞书token等敏感信息），不能上传
- `*.log` — 日志文件
- `*.xlsx`、`*.csv` — 数据文件（防止误传）
- `.vscode/`、`.idea/` — 编辑器配置
- `.DS_Store` — Mac系统文件

**简单理解：** `.gitignore` = "这些文件不要上传到GitHub"。

### sync_skills.sh 是什么？

`sync_skills.sh` 是"本地→仓库自动同步脚本"。因为技能的实际运行目录在 `~/.doubao/agent_mode/workspace/.user_skills/`，而GitHub仓库在另一个目录，需要一个脚本来把两个目录的内容保持同步。

**它做什么：**
1. 对比本地技能目录和仓库目录的文件差异（用rsync）
2. 如果本地技能有更新（比如修复了bug、优化了脚本），自动复制到仓库目录
3. 自动执行 `git add` → `git commit` → `git push`，推送到GitHub
4. 扫描本地是否有新的技能目录（不在白名单中的），有则提醒

**什么时候运行：**
- 手动运行：`bash sync_skills.sh`
- 自动运行：已整合到调度器的07每日复盘任务中（步骤7.5），每天复盘后自动执行
- 干跑模式（只检测不修改）：`bash sync_skills.sh --dry-run`

**简单理解：** `sync_skills.sh` = "把本地最新的技能代码自动备份到GitHub"。

### "白名单"是什么意思？

同步脚本里有一个 `SKILLS` 数组，列出了**需要同步到GitHub的技能目录名称**，这就是"白名单"：

```bash
SKILLS=("shangou-data-pipeline" "flux-daily-briefing")
```

**为什么需要白名单？**
- 你的本地技能目录（`~/.doubao/agent_mode/workspace/.user_skills/`）里可能有多个技能，有些是F.LUX项目的，有些可能是其他项目的（比如现在检测到的 `xhs-publisher`）
- 白名单机制确保只同步F.LUX项目相关的技能到F.LUX-SKILL仓库，不会把其他项目的技能也混进来

**"发现新技能：企微提醒是否加入白名单"是什么意思？**
- 同步脚本运行时，会扫描本地技能目录下的所有文件夹
- 如果发现一个文件夹里有 `SKILL.md`（说明是一个技能），但不在白名单中，就会判定为"新技能"
- 这时候脚本不会自动同步它（因为不确定是不是F.LUX项目的），而是通过企业微信通知你："发现新技能XXX，是否加入同步白名单？"
- 你确认后，手动把技能名称加到 `sync_skills.sh` 的 `SKILLS` 数组里，以后就会自动同步了

**简单理解：** 白名单 = "只同步这些技能"；发现新技能提醒 = "本地多了一个技能，要不要也备份到GitHub？"

---

## 分支管理与质量门禁（v2）

为了确保 main 分支里的技能都是**经过验证、稳定可用**的，采用以下分支管理流程：

### 分支策略

| 分支 | 说明 | 准入条件 |
|---|---|---|
| `main` | **稳定主线**，只放经过验证的技能 | 连续3天执行无报错 + 用户审查合并 |
| `dev/<技能名>` | **验证区**，新技能或有修改的技能先到这里 | 本地技能有变化时自动推送 |

### 完整流程

```
本地技能代码更新（修复bug/优化脚本）
    ↓
07每日复盘 → 步骤7.5 技能复盘与GitHub同步：
  ├─ 1. 检查当日技能执行是否报错
  ├─ 2. 更新 skill_status.json（连续无报错天数）
  ├─ 3. 有报错 → 自动创建GitHub Issue（优化建议）→ 用户确认后修复
  ├─ 4. 代码有变化 → 推送到 dev/<技能名> 分支，重置验证天数为0
  └─ 5. 连续3天无报错 → 提醒用户可合并到main（创建PR）
    ↓
用户在GitHub审查PR → 点"Merge"合并到main
    ↓
main 分支保持稳定
```

### 质量门禁规则

1. **新技能/代码变化** → 自动推送到 `dev/<技能名>` 分支，**不直接进main**
2. **连续3天无报错** → 才允许合并到main（`--promote` 命令会检查天数）
3. **有报错** → 自动创建GitHub Issue（标签：技能优化建议），**不自动改代码**，用户确认后才修复
4. **代码有变化** → 自动重置连续无报错天数为0，需要重新验证3天
5. **用户审查** → 合并到main需要用户在GitHub上审查PR后手动点Merge

### 常用命令

```bash
# 查看各技能验证状态（连续无报错天数、当前分支）
bash sync_skills.sh --status

# 检测变化并推送到dev分支（默认行为）
bash sync_skills.sh

# 只检测不修改
bash sync_skills.sh --dry-run

# 连续3天无报错后，合并到main
bash sync_skills.sh --promote shangou-data-pipeline

# 创建GitHub Issue（技能优化建议）
bash sync_skills.sh --issue shangou-data-pipeline "报错标题" "详细描述"
```

### 技能状态文件

状态存储在 `~/.config/flux-skills/skill_status.json`，记录每个技能的：
- 当前所在分支
- 连续无报错天数
- 最后执行日期和状态
- 最后报错信息
- 是否已合并到main

---

## 调度链（v8.0）

```
08:30  shangou-data-pipeline（数据采集与总表更新）
  ↓ (完成后≥30分钟)
  ↓  flux-daily-briefing（每日经营简报）
  ↓
20:00  07每日复盘与自我进化
  ↓
次日08:30 循环（周日→01资料归类）
```

## 安装

### 前置依赖
- Python 3.10+
- Chrome 浏览器（CDP 远程调试模式）
- lark-cli（飞书云空间操作）
- wecom-cli（企业微信通知）

### shangou-data-pipeline 安装
```bash
cd shangou-data-pipeline
pip install -r requirements.txt
bash scripts/setup.sh   # 交互式安装向导（7步）
```

### flux-daily-briefing 安装
```bash
cd flux-daily-briefing
pip install openpyxl pandas  # 依赖
# 无需安装向导，直接调用脚本
```

## 使用

### 数据采集
```bash
python3 shangou-data-pipeline/scripts/run_pipeline.py
```

### 生成简报
```bash
python3 flux-daily-briefing/scripts/generate_briefing.py \
  --excel <总表路径.xlsx> --date 20260916 --out <输出目录>
```

### 企微通知
```bash
python3 flux-daily-briefing/scripts/notify_wecom.py \
  --summary <简报JSON> --date 20260916
```

## 目录结构

```
F.LUX-SKILL/
├── shangou-data-pipeline/
│   ├── SKILL.md              # 技能说明文档
│   ├── requirements.txt      # Python依赖
│   ├── references/
│   │   └── schema.md         # 8张表结构定义
│   └── scripts/              # 17个脚本（采集/整合/导出/同步/安装）
├── flux-daily-briefing/
│   ├── SKILL.md              # 技能说明文档
│   └── scripts/
│       ├── generate_briefing.py  # 简报生成（11章节）
│       └── notify_wecom.py       # 企微通知
├── sync_skills.sh           # 本地→仓库同步脚本
├── .gitignore
└── README.md
```

## 同步机制

本地技能目录（`~/.doubao/agent_mode/workspace/.user_skills/`）与本仓库的同步：

```bash
# 检测本地技能变化，同步到仓库并推送
bash sync_skills.sh
```

同步脚本会：
1. 对比本地技能源码与仓库版本（排除 data/、__pycache__/、config.json）
2. 如有变化，自动复制到仓库、提交、推送到 GitHub
3. 检测是否有新技能目录，自动添加到仓库

## 注意事项

- **数据文件不入库**：`data/` 目录（含总表Excel、CSV累积表、原始报表）不提交到仓库
- **配置文件不入库**：`config.json`（含本地路径、飞书token等）不提交到仓库
- **新电脑部署**：克隆仓库后需重新运行 `setup.sh` 生成配置文件和登录态
