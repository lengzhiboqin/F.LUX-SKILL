---
name: douyin-daily-analysis
description: 抖音账号「帮主出发啦」每日数据分析与内容策划工作流。自动登录抖音创作者后台采集数据（数据中心+内容管理），搜索抖音最近7天热门视频（5条多元化类型），基于热门视频二创生成5个选题推荐+详细分镜头脚本，追加到固定智能文档（拍摄策划+分镜头脚本两个页面），上传微盘备份，推送企微群消息简易版。当用户提到"抖音每日数据分析""帮主出发啦每日运营""抖音账号数据复盘""抖音内容策划""定时任务触发的抖音分析"，或需要分析抖音创作者后台数据、生成抖音拍摄策划和分镜头脚本、推送抖音运营群消息时使用。
---

# 抖音账号每日数据分析工作流（帮主出发啦）

## 核心原则
- 数据必须真实，来自抖音创作者后台，禁止编造
- 热门视频必须抖音网页版直搜，禁用通用搜索
- 所有内容追加到固定智能文档，不创建当日独立文档
- 群消息必须在两个文档页面都追加完成后推送
- 称呼用户为"老大"，实事求是不夸大

## 账号与配置
完整账号背景、人设、店铺情况、固定文档ID、webhook地址见 [references/account-profile.md](references/account-profile.md)。

关键配置速查：
- 创作者后台：https://creator.douyin.com/
- 数据中心：https://creator.douyin.com/creator-micro/data-center/operation
- 内容管理：https://creator.douyin.com/creator-micro/content/manage
- 固定文档docid：a1_ANsAG3j5ALUCNHCSGna7YQFWUc2cj_a
- 拍摄策划page_id：zyqjZU
- 分镜头脚本page_id：6Ag4k9
- 微盘备份folder_id：fiz9h33ph3TDtaBh49IM7gM6KQNMg4bnhmvdGqkLr56_n8XNZS_MgI3gTZRYJ_aV3S8r83lOd3pb_1M1Z9Wdae6A
- wecom-cli路径：/home/user/.npm-global/bin/wecom-cli（必须用1.2.1新版本）

---

## 工作流程（8步）

### Step 1：登录抖音创作者后台，检查登录态

1. 用浏览器打开 https://creator.douyin.com/
2. 检查是否已登录：
   - 已登录 → 直接进入，关闭首页弹窗（"欢迎体验新版首页"引导，可能有2步）
   - 未登录 → 立即通知用户重新登录，通过interaction.request_action让用户扫码，不要尝试绕过
3. 登录态丢失时不要继续后续步骤，明确告知失败原因

### Step 2：采集数据中心数据

1. 打开数据中心：https://creator.douyin.com/creator-micro/data-center/operation
2. 记录实际统计周期（如9.13-9.19），不要写"近7日"模糊表述
3. 采集以下数据：
   - 账号总览：播放量、互动率、完播率、作品数、粉丝净增、同类作者对比、较昨日变化
   - 作品数据：总播放、总点赞、总评论、总分享、5秒完播率、2秒跳出率、封面点击率、平均播放时长
   - 粉丝数据：总粉丝、粉丝净增、吸粉量、脱粉量、获赞
4. 向下滚动查看完整数据，不要只看首屏

### Step 3：采集内容管理作品数据

1. 打开内容管理：https://creator.douyin.com/creator-micro/content/manage
2. 关闭可能弹出的"评论管理""合集管理"等弹窗
3. 重点记录最近3-5条作品数据：
   - 标题、发布时间、时长
   - 播放量、点赞数、评论数、分享数、收藏数
   - 完播率、2秒跳出率、5秒完播率（如有）、吸粉量
   - 如有评论，记录评论内容（评论是重要的互动信号和选题灵感）
4. 找出数据最好和最差的作品，分析原因

### Step 4：搜索抖音最近7天热门视频（5条）

**必须用抖音网页版直搜，禁用通用搜索。**

1. 打开 https://www.douyin.com，确认已登录（未登录时搜索会强制弹窗且无关闭按钮）
2. 搜索不同类型关键词，每个类型搜一次，确保多元化：
   - 搞笑反转类：搞笑、反转、万万没想到、名场面
   - 打工人日常类：打工人、上班哪有不疯的、精神状态belike
   - 守店日常类：守店日常、开店vlog、实体店、老板娘日常
   - 棋牌类：象棋盲棋、麻将反转、斗地主名场面（账号当前方向）
   - 情绪共鸣类：致自己、长大、成年人的崩溃、深夜emo
   - 正能量温暖类：暖心瞬间、正能量、小人物、双向奔赴
3. 筛选条件：最近7天发布（优先最近3天），点赞1万以上优先，至少5000以上
4. 最终选5条，必须覆盖至少4种不同类型，类型配比：搞笑反差40%、真实记录30%、情感共鸣20%、正能量温暖10%
5. 每条视频必须记录：标题、链接、发布时间、作者、时长、点赞/评论/分享/收藏（真实公开数据）、核心结构、数据好的原因、可二创切入点
6. 如果最近7天高赞短视频极少（泛词搜索常见问题），放宽筛选或用更具体关键词，确实找不到时可用最近7-14天视频但必须标注发布时间

### Step 5：生成拍摄策划Markdown

1. 基于Step 2-4的数据，生成拍摄策划
2. 文件命名：抖音拍摄策划（YYYY年MM月DD日）.md
3. 保存到项目主目录
4. 开头加日期分隔线：
```markdown
---
## YYYY年MM月DD日
---
```
5. 内容结构（完整模板见 [references/output-templates.md](references/output-templates.md)）：
   - 数据概览（账号总览+作品数据+粉丝数据+最新作品详细）
   - 重大发现与亮点总结
   - 问题诊断（3条，每条有数据支撑+原因+优化建议）
   - 5条热门视频分析（含真实数据+核心结构+二创切入点）
   - 5个选题推荐（每个含参考视频链接+真实数据+核心结构+数据好原因+二创内容表格+时长+互动引导+拍摄要点）
   - 推荐优先级表格（优先级、选题、参考视频、点赞数、理由）
   - 最终建议（1-2句话明确拍哪个）
6. 选题必须基于热门视频二创，结合守店人+运营人设，不要直接照搬原视频
7. 互动引导必须具体有共鸣，不能泛泛"你怎么看"

### Step 6：生成分镜头脚本Markdown

1. 为今日重点推荐选题写详细分镜头脚本
2. 文件命名：分镜头脚本-选题标题（YYYY年MM月DD日）.md
3. 保存到项目主目录
4. 开头加日期分隔线：
```markdown
---
## YYYY年MM月DD日：《选题标题》
---
```
5. 结构（完整模板见 [references/output-templates.md](references/output-templates.md)）：
   - 脚本标题（总时长、风格、人物、场景）
   - 每个镜头必须包含7个字段：景别、镜头语言、画面内容、台词/字幕、音效/BGM、演员指导、拍摄要点
   - 整体拍摄要点总结6个部分：节奏控制、音效关键节点、人物表情变化线、声音指导、道具准备、后期剪辑要点
6. 时长控制在13-30秒，不要超过30秒（账号历史验证超30秒完播率大幅下降）
7. 前3秒必须有钩子，最后一帧必须有具体互动引导

### Step 7：追加固定文档 + 上传微盘备份

**必须使用wecom-cli新版本路径：/home/user/.npm-global/bin/wecom-cli**

1. 检查wecom-cli授权状态：
```bash
/home/user/.npm-global/bin/wecom-cli auth show --status
```
输出`authorized`才可继续。

2. 追加拍摄策划到固定文档「每日拍摄策划」页面：
```bash
/home/user/.npm-global/bin/wecom-cli smartpage pages append --json '{"docid":"a1_ANsAG3j5ALUCNHCSGna7YQFWUc2cj_a","page_id":"zyqjZU","content_type":"markdown","file_path":"<拍摄策划文件路径>"}'
```
成功标志：status: success, errcode: 0

3. 追加分镜头脚本到固定文档「分镜头脚本」页面：
```bash
/home/user/.npm-global/bin/wecom-cli smartpage pages append --json '{"docid":"a1_ANsAG3j5ALUCNHCSGna7YQFWUc2cj_a","page_id":"6Ag4k9","content_type":"markdown","file_path":"<分镜头脚本文件路径>"}'
```
成功标志：status: success, errcode: 0

4. 上传两个Markdown文件到微盘备份：
```bash
/home/user/.npm-global/bin/wecom-cli disk files upload --json '{"folder_id":"fiz9h33ph3TDtaBh49IM7gM6KQNMg4bnhmvdGqkLr56_n8XNZS_MgI3gTZRYJ_aV3S8r83lOd3pb_1M1Z9Wdae6A","file_path":"<文件路径>"}'
```
拍摄策划和分镜头脚本都要上传，不要只传一个。

### Step 8：推送企微群消息

**必须在两个文档页面都追加成功后推送。必须用python + urllib，不要用curl + %格式化（内容含"0%"会冲突）。**

1. 构造markdown内容（完整模板见 [references/output-templates.md](references/output-templates.md)）：
   - 标题：📊 抖音账号每日数据分析（YYYY年MM月DD日）
   - 数据概览：最新作品播放/点赞/评论/完播率/2秒跳出率（简洁列表）
   - 问题诊断：3条以内核心问题
   - 今日推荐选题：1个重点推荐，含标题、参考视频链接、点赞数、核心亮点
   - 文档链接：拍摄策划+分镜头脚本（指向固定文档）
   - 结尾：一句话行动建议

2. 用python推送：
```python
import urllib.request
import json

webhook = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=95eccdcd-994b-4c57-a7e0-4ab7c502ce11"
content = "..."  # markdown内容
data = {"msgtype": "markdown", "markdown": {"content": content}}
req = urllib.request.Request(webhook, data=json.dumps(data).encode('utf-8'), headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(req, timeout=10) as response:
    result = json.loads(response.read().decode('utf-8'))
    # 成功标志：errcode == 0
```

---

## 异常处理

### 登录态丢失
- 立即通知用户重新登录，不要继续后续步骤
- 不要尝试绕过登录或用通用搜索代替后台数据

### 作品突然爆了（播放量远超平时）
- 及时提醒并分析爆点原因（开头钩子？话题？发布时间？）
- 在群消息中重点标注

### 作品被限流或数据异常
- 检查是否有违规内容
- 及时提醒并给出应对建议

### 粉丝增长异常
- 及时提醒，分析原因（哪条作品吸粉？评论区有什么？）

### wecom-cli报错
- 确认使用新版本路径 /home/user/.npm-global/bin/wecom-cli
- 旧版本1.1.0会报"cli token expired"，必须升级到1.2.1
- 检查授权状态：auth show --status

### 群消息推送失败
- 确认用python + urllib，不要用curl + %格式化
- 检查webhook地址是否正确
- 检查内容是否有特殊字符导致JSON解析失败

---

## 参考文件

- [references/account-profile.md](references/account-profile.md) — 账号背景、人设、店铺情况、固定文档ID、webhook、wecom-cli配置
- [references/output-templates.md](references/output-templates.md) — 拍摄策划模板、分镜头脚本模板、群消息模板
- [references/pitfalls.md](references/pitfalls.md) — 常见坑与解决方案（登录态、热门搜索、wecom-cli、群消息推送、内容创作）

执行过程中遇到具体问题时，先查pitfalls.md，大部分已验证问题都有解决方案。
