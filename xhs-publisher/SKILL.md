---
name: xhs-publisher
description: "小红书美妆即时零售知识分享系列内容生成与全自动发布技能（AI 环境无关，任意 AI 均可复用）。涵盖：开放选题（与用户互动确认，30期规划仅作参考框架）、发布前联网调研、小红书爆款文案生成（标题/正文/标签）、网感封面图生成、用户确认流程、企业微信/其他渠道提醒、未确认自动兜底发布（30分钟提醒+60分钟自动发布）、小红书创作平台全自动发布（基于 Playwright 独立 CLI 脚本，上传封面/填标题正文/坐标点击发布按钮）。当用户要求生成小红书美妆笔记、发布小红书内容、美妆即时零售知识分享、小红书避坑系列、推荐选题、或定时任务触发小红书内容生成与发布时使用此技能。"
---

# 小红书美妆内容生成与全自动发布

> **环境无关设计**：本技能不绑定任何特定 AI 平台的工具。所有操作均基于通用能力描述 + 独立可执行的 Playwright Python 脚本，可在任意 AI 环境（ChatGPT、Claude、Gemini、豆包、Kimi 等）中复用。

## 任务概述

自动生成美妆即时零售知识分享系列的小红书笔记（文案+封面图），**选题开放、与用户互动确认**（30期规划仅作参考框架），用户确认内容后全自动发布到小红书创作平台。支持未确认自动兜底：30分钟提醒，60分钟自动发布。

## 环境准备（首次使用）

### 必需
1. **Python 3.8+**
2. **Playwright**：
   ```bash
   pip install playwright
   playwright install chromium
   ```
3. **联网搜索能力**：任意 AI 自带的搜索功能，或通过搜索引擎 API / requests 库实现
4. **图片生成能力**：任意 AI 自带的图片生成功能（DALL-E / Midjourney API / Stable Diffusion / 其他），或手动提供封面图

### 可选
- **wecom-cli**（企业微信通知）：`npm install -g @wecom/cli`，然后 `wecom-cli auth init` 扫码授权
- **系统定时任务**（兜底自动发布）：Linux cron / Windows 任务计划程序 / APScheduler

### 配置
复制 `config.example.json` 为 `config.json`，填入你的配置（企微 chat_id、浏览器偏好等）。

## 执行流程

### 第一步：确定本期主题（开放互动）
- **选题是开放的，不固定按系列顺序执行**。30期系列规划仅作为**参考框架和选题库**，见 [references/content-guide.md](references/content-guide.md)
- 选题确定方式（按优先级）：
  1. **用户明确指定选题** → 直接使用用户指定的选题，不限于系列规划内
  2. **用户要求推荐选题** → 从系列规划中推荐2-3个候选选题（含简要说明），让用户选择
  3. **定时任务自动触发且用户未指定** → 可按系列顺序推荐下一期作为候选，但**生成前必须先与用户确认选题**，不得直接跳过选题确认环节
- 确保主题不与已生成的重复

### 第二步：发布前调研（强制）
- **每期内容生成前必须进行联网调研**，获取真实数据和行业资料，不胡编乱造
- 使用 AI 自带的搜索功能，或通过搜索引擎 API / requests 库检索
- 调研关键词建议：美妆即时零售 + 本期主题关键词、美妆线上店运营数据、行业报告
- 调研结果需在内容中体现（数据、案例、趋势），增强可信度

### 第三步：生成笔记内容
- 目标受众：想做美妆即时零售的商家/创业者
- 内容目的：通过知识干货建立专业人设，吸引潜在客户私信咨询
- 格式要求（详细规范见 [references/content-guide.md](references/content-guide.md)）：
  - 标题：≤20字，知识干货型+数字+痛点
  - 正文：550-650字（硬上限900字），4-6个知识点（是什么+为什么+怎么做），emoji，第一人称
  - 标签：5-8个，放内容最后，用中性词（#开店日常 #小本创业），禁#副业赚钱
  - 结尾：金句+评论区互动引导，不喊"私信领资料"
- **敏感词红线**：美团/闪购/外卖/淘宝/拼多多等必须替换（详见参考文件）
- 将完整正文（含标签）保存到 `output/body_XX.txt`（XX为期数或日期）

### 第四步：生成封面图
- 使用 AI 自带的图片生成能力（或手动提供封面图）
- 风格：真实场景背景 + 超大粗体字（白/红/黄+黑描边）+ 黑色笔刷标签条 + 对话气泡贴纸
- 尺寸：竖版3:4（建议 1536x2048）
- 禁任何平台logo/水印
- 涉及证件类改用文件夹/清单中性表述
- 将封面图保存到 `output/cover_XX.png`

### 第五步：呈现内容并请求确认
- 清晰展示：封面图 + 标题 + 正文 + 标签
- 告知用户本期选题（如适用，可说明是系列第几期）
- 询问用户是否确认发布
- 所有生成文件保存在 `output/` 目录下，用户可直接访问

### 第六步：企微提醒 + 创建兜底任务（内容呈现后立即执行，可选）

> 如果配置了企微通知和兜底机制，执行此步骤；否则跳过。

#### 6.1 企微第一次提醒
通过 wecom-cli 发送 Markdown 消息：
```bash
wecom-cli message aibot send --json '{"chat_id":"你的ID","msg_type":"markdown","markdown":{"content":"老大，今天第X期「标题」内容已生成，请回复确认发布。30分钟未确认将再次提醒，60分钟未确认将自动发布。"}}'
```
发送方法和配置见 [references/content-guide.md](references/content-guide.md) 的"企微提醒配置"章节。

#### 6.2 写入状态文件
将当期信息保存到 `publish_state.json`（路径可在 config.json 中配置）：
```json
{
  "issue": 10,
  "title": "笔记标题",
  "cover_path": "./output/cover_10.png",
  "body_path": "./output/body_10.txt",
  "created_at": "2026-09-16T10:35:00"
}
```

#### 6.3 创建兜底任务（通过系统定时任务）
- **任务A（30分钟后提醒）**：用系统 cron / Windows 任务计划 / APScheduler 创建一次性任务，30分钟后执行检查脚本，未发布则企微第二次提醒
- **任务B（60分钟后自动发布）**：创建一次性任务，60分钟后执行检查，未发布则调用发布脚本自动发布

### 第七步：用户确认发布时的流程

当用户回复"确认发布"时：

1. **取消兜底任务**（如果创建了）：删除系统定时任务A和B
2. **清空状态文件**
3. **执行自动发布**（调用 Playwright 脚本）：
   ```bash
   python scripts/xhs_auto_publish.py \
     --cover ./output/cover_XX.png \
     --title "笔记标题" \
     --body-file ./output/body_XX.txt \
     --config config.json
   ```
   - 首次运行会打开浏览器窗口，需要用户扫码登录小红书
   - 登录后脚本自动完成：切换图文→上传封面→填标题→填正文→点击发布→验证成功
   - 加 `--headless` 可无头运行（需先完成一次有头登录保存状态）
4. **验证发布成功**：脚本会自动验证 URL 是否跳转到 success 页面
5. **到笔记管理页确认**（可选）：
   ```bash
   python scripts/xhs_auto_publish.py --check "标题关键词"
   ```

### 第八步：兜底任务触发时的流程

#### 任务A（30分钟提醒）触发时：
1. 读取 `publish_state.json` 获取当期信息
2. 调用 `python scripts/xhs_auto_publish.py --check "标题关键词"` 检查是否已发布
3. 已发布 → 任务结束，取消任务B
4. 未发布 → 企微发送第二次提醒

#### 任务B（60分钟自动发布）触发时：
1. 读取 `publish_state.json` 获取当期信息
2. 检查是否已发布
3. 已发布 → 企微通知"已发布"，任务结束
4. 未发布 → 调用发布脚本自动发布，成功后企微通知

## 验收标准

发布成功必须同时满足：
1. 浏览器URL跳转到 `https://creator.xiaohongshu.com/publish/success`（或URL含 published=true）
2. 笔记管理页能搜到该笔记标题（可用 `--check` 命令验证）
3. 标题、正文、标签与生成内容一致
4. 封面图已上传且显示正确

内容质量验收：
1. 标题≤20字
2. 正文550-650字（不超过900字）
3. 4-6个知识点，每个含是什么+为什么+怎么做
4. 标签5-8个，放最后，无敏感词
5. 无违禁词（美团/闪购/外卖/淘宝/拼多多等）
6. 生成前有联网调研

## 关键技术要点（已验证，与AI无关）

这些是小红书网站本身的技术事实，任何AI/工具都需要遵循：

### 发布按钮点击（最关键）
- 底部红色"发布"按钮是自定义元素 `<xhs-publish-btn>`，**JS .click() 无效**
- 必须用鼠标坐标点击，按钮内相对位置 **xr=0.60, yr=0.5**
- 窄窗口(<1000px)时按钮可能被截断，用 `document.body.style.zoom='0.6'` 缩小页面
- Playwright 中用 `page.mouse.click(x, y)` 实现坐标点击

### 富文本编辑器
- 正文选择器：`.tiptap.ProseMirror`
- 输入 #标签 会弹出 Tippy 话题下拉，需用 JS 隐藏 `.tippy-box` 和 `[data-tippy-root]`
- 编辑器渲染有时序，填前需等待元素出现

### 已验证死路（勿再尝试）
- JS .click()、mousedown/up序列、ref click、Tab+Enter、shadow穿透
- 直接JS隐藏d-menu（SPA不重排）

## 脚本参考

- [scripts/xhs_auto_publish.py](scripts/xhs_auto_publish.py)：Playwright 独立 CLI 发布脚本
  - 发布：`python xhs_auto_publish.py --cover <图> --title <标题> --body <正文>`
  - 从文件读正文：`--body-file <路径>`
  - 检查已发布：`--check <关键词>`
  - 无头模式：`--headless`
  - 指定配置：`--config config.json`
- [config.example.json](config.example.json)：配置模板（复制为 config.json 使用）
- [references/content-guide.md](references/content-guide.md)：30期规划、内容格式、封面风格、敏感词、行业数据、企微配置、状态文件格式
- [output/](output/)：生成的封面图、正文文件、状态文件的输出目录

## 目录结构

```
xhs-publisher/
├── SKILL.md                    # 本文件（技能说明）
├── config.example.json         # 配置模板
├── scripts/
│   └── xhs_auto_publish.py    # Playwright 自动发布 CLI 脚本
├── references/
│   └── content-guide.md       # 内容规范 + 30期规划 + 行业数据
└── output/                     # 输出目录（封面、正文、状态文件）
```
