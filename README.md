# F.LUX-SKILL

F.LUX 品牌运营自动化技能包。将日常工作流拆解为可复用的 AI 技能，通过调度器串行调度执行。

## 技能清单

| 技能 | 说明 | 状态 |
|---|---|---|
| `shangou-data-pipeline` | 美团闪购商家端每日数据采集管线：自动采集8张报表→整合累积总表→清理原始数据→导出Excel总表→飞书同步 | ✅ 已验收 |
| `flux-daily-briefing` | F.LUX品牌每日经营简报生成：基于总表8张表做全维度经营分析（11章节复杂版），支持飞书上传和企微通知 | ✅ 已建 |

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
