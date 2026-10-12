# 恢复、变更处理与汇报（第三阶段）+ 只读查询

## 一、错误码 → 处置

| 错误码 | 含义 | 自动做什么 | 何时找人 |
|---|---|---|---|
| `LOGIN_REQUIRED` | 落在登录/验证页 | 保存任务，给出页面与操作提示 | **用户本人**扫码/密码/验证码 |
| `BROWSER_UNAVAILABLE` | CDP 端点不可达 | 提示启动带调试端口的浏览器并登录 | 用户启动/登录 |
| `TIMEOUT` | 下载/轮询超时 | 检查已有导出任务再等待或重试，避免重复提交 | 达到上限后报告平台/任务 |
| `STRUCTURE_CHANGED` | 表头/列数变化 | **阻断**，保存差异与原件 | 用户确认契约/映射后再回归 |
| `DATE_MISMATCH` | 文件名/页面日期与任务不符 | 阻断 | 确认是否拿错任务 |
| `SCOPE_MISMATCH` | 范围不符（渠道/门店） | 阻断 | 确认口径 |
| `EMPTY_DOWNLOAD` | 只有表头无数据行 / 未产生文件 | 阻断；确认拿错任务时重取 | 新结构或持续异常时确认 |
| `ZERO_BYTES` | 0 字节 | `failed`，**不得当无事件** | 持续出现时确认 |
| `PARSE_FAILED` | 读不了/校验和不符 | `failed`，保存原件 | 持续出现时确认 |
| `TASK_NOT_FOUND` | 导出任务找不到 | `failed`，记录请求摘要 | 平台异常持续时确认 |
| `PLATFORM_ERROR` | 其他平台异常/依赖缺失 | `failed`，记录类型与信息 | 依赖缺失需装依赖 |
| `NO_DATA_UNVERIFIED` | 声称无事件但没有平台证据 | 降级 `failed` | 需补平台证据 |
| `UNSUPPORTED_DOWNLOAD` | **平台/采集器当前不具备该能力**（如闪购定向历史日回采） | `blocked`，给出人工替代步骤 | 需人工设日期导出，或先接入回采路径并做平台实测 |

## 二、有限重试（不无限循环）

- 默认 **最多 3 次**（首次 + 追加 2 次）；每次记录尝试次数与结果。
- `decide_recovery(manifest, error, extra_retries=2)` → `RecoveryDecision{action, reason, task_key, attempts_left}`：
  - 可重试类（超时、页面未加载、布局小变化）→ `retry`
  - 登录/结构/语义 → `wait_user` 或 `stop`
  - 已达上限 → `stop`，**报告平台/任务与下一步**
- 平台限流时按上限等待或如实报告受阻，**不用无限重试凑“成功”**。

## 三、断点恢复 `resume`

- **只重跑受阻任务**，已 `validated/no_data` 的不重采。
- **沿用同一业务日期与批次身份**；跨日恢复**不悄悄改成今天的任务**。
- 已提交批次直接跳过（`skipped_already_committed`），不重复计数。
- 恢复后若整批合格且给了 `--db`，会在同一批次身份下完成入库。
- 重新规划新批次必须**明确记录**。

```bash
python scripts/flux.py resume --manifest _data/_batches/<batch_id>/manifest.json \
    --config config.json --rules references/cleaning_rules.json \
    --master references/store_master.json --db _data/flux.db
```

## 四、变化分级（`classify_change`）

| 变化 | 能自动做什么 | 必须停下确认什么 |
|---|---|---|
| **布局**（按钮位置变，业务含义不变） | 用**唯一可识别**的同义定位重试；核对页面日期/范围/下载结构与内容后继续，记录适配方式 | 定位有歧义、要改请求指标/范围、定位能力不足 |
| **呈现**（契约与含义不变） | 在**已明确规则内**调整页面定位配置；新配置**先在测试任务验证**、通过后记录版本，旧配置可回退 | 验证不通过就不得推广到正式任务 |
| **列变化**（增/删/改名/顺序/类型） | 保存变化前后结构、原件、受阻任务，产出候选适配说明与回归范围 | 核实业务含义、确认契约/映射后**才**更新并回归恢复；不得自动补空/改名/丢列 |
| **语义**（单位/日期/粒度/渠道/指标算法） | 给出**影响哪些表、指标、历史结果**的说明 | **业务规则变更需用户批准**，程序不得自行改口径 |

自动适配**仅限**可验证的页面定位与明确规则内的配置；代码级修复属于开发维护，
**不承诺运行时让 agent 自由改写业务代码**。变更记录含：前后版本、证据、验证结果、回退方法。

## 五、汇报模板（只报实际完成的环节）

成功：

```text
【成功】批次 <ID>，目标普通表 <日期>、评价 <日期>、快照 <起止>
采集：<计划/完成数>；补漏 <数>；确认无事件 <数>
入库：新增 <数>、替换 <数>；成功版本 <ID>
自检：<方式/表/日期范围/样本数>，<结论>
保留：<起止>；清理过期记录 <数>；空间维护 <结果/未执行>
提示：<待核口径/未匹配门店/不可回溯等实际限制，无则写无>
证据：<报告/清单路径>
```

未完成：

```text
【未完成】批次 <ID>，受阻于 <采集/原件验收/清洗/入库/自检/维护>
原因：<平台/表/日期/范围/具体失败>
已完成：<可复用材料>；本次库是否提交：<是/否>
当前可查：成功版本 <ID>，覆盖至 <各表实际日期及缺口>
已尝试：<次数和结果>；下一步：<自动恢复步骤或用户具体操作>
证据：<原件/差异/日志路径>
```

**只采集成功时不许写「已入库」**；「采集全合格但提交后自检发现问题」也走**未完成**模板并注明已提交。
日志与汇报**不含凭据**。

```bash
python scripts/flux.py report --manifest _data/_batches/<batch_id>/manifest.json
```

## 六、只读查询（`scripts/query.mjs`，Node 24+）

```bash
node scripts/query.mjs help
node scripts/query.mjs catalog --db _data/flux.db --table 门店经营指标
node scripts/query.mjs status  --db _data/flux.db
node scripts/query.mjs stores  --db _data/flux.db [--id S0001]
node scripts/query.mjs get     --db _data/flux.db --table 门店经营指标 \
     --date 2026-09-30 --metric 有效订单金额 --store S0001 --scope all --limit 100
node scripts/query.mjs lineage --db _data/flux.db --table 门店经营指标 --date 2026-09-30
node scripts/query.mjs audit   --db _data/flux.db
```

- `get` 返回**数值、单位、粒度、覆盖、来源 SHA-256**，方便 AI 定位与复核。
- **待核指标不出正式值**（`status=pending`，`value=null`）。
- 只读保证：`DatabaseSync(path, {readOnly:true})` + `PRAGMA query_only=ON`；
  `audit` 跑完整性检查但**不改库**。
- 库不存在会返回明确 `error`，不是崩溃。

## 七、跨模块衔接检查清单

- [ ] 采集与入库用**同一个 `batch_id`**（`_batch.batch_id` 可核对）。
- [ ] 独立清洗**没有**触发浏览器；独立查询**没有**触发写库。
- [ ] 一个任务受阻时**整批未提交**（`SELECT * FROM _batch` 无新记录）。
- [ ] 恢复**没有**无限重试；已达上限即停并给出人工步骤。
- [ ] 过期只被 `retain`/成功入库处理，只读自检**只报告**。
