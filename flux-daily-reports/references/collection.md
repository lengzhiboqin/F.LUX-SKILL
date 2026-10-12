# 采集模块（第一阶段）

职责：**确定日期与缺口 → 从两平台准确拿到报表 → 核对原件**。
边界：**不清洗、不写业务库**。产出原件、批次清单、范围与结构验收结果、失败信息。

## 一、19 表清单（唯一来源：`references/table_registry.json`）

| 平台 | 表 | 类型 | 默认偏移 |
|---|---|---|---|
| 牵牛花 | 门店经营指标 / 门店商品指标明细 / 品类分析 / 配送指标明细 / 配送距离分布 / 门店流量排行 / 门店流量_分来源 / 商品流量_店内 / 商品流量_店外 / 门店营业时长 | daily | T-1 |
| 牵牛花 | 商品流量_近30天 | **snapshot** | 窗口 T-30~T-1 |
| 闪购 | 门店财务明细 / 门店成交明细 / 商品数据 / 流量明细(新) / 流量渠道明细(新) | daily | T-1 |
| 闪购 | 问题订单数据 / 售后订单数据 | event | T-1 |
| 闪购 | 评价数据 | event | **T-2** |

**不得在别处再写一份硬编码表名。** 注册表由 `scripts/common/registry.py` 加载并自检。

## 二、七日缺口怎么算

1. 按 `Asia/Shanghai` 固定业务日 **T**（跨午夜不变，非法日历日期直接报错）。
2. 逐表生成**窗口任务**：普通日表 `T-7 ~ T-1`，评价 `T-8 ~ T-2`（各含两端 7 天）。
3. 再叠加**目标日任务**（T-1 / T-2 / 快照窗口结束 T-1）；与窗口重叠的**按 `task_key` 去重**，
   同一任务不导出两次。
4. 用**台账**（`_data/_ledger.json`）逐任务比对：`validated / no_data` 视为已完成。
   **不以最大日期推断完整，不遍历全历史。**
5. 补漏任务排在前面，目标日排在后面。
6. `force=True` 时忽略台账强制重采（用于验收所需的连续三轮真实采集）。

`task_key` = 业务身份（平台|表|业务日期|窗口|范围|契约版本）的稳定摘要，**不含采集时间**；
不同渠道/门店范围**不能共用**同一个键，否则会互相覆盖。

## 三、原件落盘与验收

- 原件**逐批留存**：`_data/raw/<batch_id>/…`。批次之间不互相覆盖，也不移动到固定历史目录。
- 每项任务记录：平台、表、业务日期/窗口、渠道/门店范围、采集时间、状态、结构版本；
  有文件时记录**路径、字节数、完整 SHA-256** 和导出任务 ID（或直下证据）。

`validate_raw` 的判定（对应 spec §3.2）：

| 情况 | 结果 |
|---|---|
| 文件字节数/SHA-256 与登记不符 | `failed(PARSE_FAILED)` |
| 0 字节 | `failed(ZERO_BYTES)` —— **不是**无事件 |
| 读不了 / 解析失败 | `failed(PARSE_FAILED)` |
| 表头任意位置与基线不一致、列数不符 | `blocked(STRUCTURE_CHANGED)` —— **不自动补列、不重排、不改名** |
| 牵牛花文件名不含期望日期 | `blocked(DATE_MISMATCH)` |
| 渠道拆采但文件名无 `_ch<渠道>` | `blocked(SCOPE_MISMATCH)` |
| 只有表头、无数据行 | `blocked(EMPTY_DOWNLOAD)` —— 需平台无事件证据才算完成 |
| 全部合格 | `validated`（并写入结构证据基线日期） |

**无事件**（`no_data`）必须带平台证据（导出任务 ID / 无事件响应 / 截图），否则降级为
`failed(NO_DATA_UNVERIFIED)`。问题订单、评价、售后三表才允许无事件。

### 尚不具备的能力（如实记录，禁止假通过）

| 能力 | 现状 | 行为 |
|---|---|---|
| 闪购**定向历史日**回采 | **未支持**：gen2 流量表硬编码「昨日」；gen1 非 daily 分支不操作日期控件 | 直接 `blocked(UNSUPPORTED_DOWNLOAD)`，**不冒用昨日数据冒充指定日期**；提示人工在页面设日期后导出，或先接入 `shangou_collect` 回采路径并做平台实测 |
| 闪购门店/渠道范围筛选 | **未支持** | `blocked(SCOPE_MISMATCH)` |
| 滚动快照历史回溯 | 仅当平台支持时补采 | 不支持则记入 `unsupported_snapshots` 明确报告 |
| 牵牛花渠道拆采 | 支持，文件名带 `_ch<渠道>` | 缺失即 `blocked(SCOPE_MISMATCH)` |

`UNSUPPORTED_DOWNLOAD` 是本次为「能力缺口」新增的错误码（原契约只有失败类代码），
用于把**「平台不支持」**与**「采集失败」**分开，避免把能力缺口记成成功。需随交付一起确认。

## 四、两个平台怎么采

- **牵牛花**：复用 `qianniu_collect.Collector`，通过 **CDP 连接已登录浏览器**
  （`cdp_host`/`cdp_port`），按 `beginDate`/`startDate` 设置日期后走任务中心导出下载。
  渠道拆采时文件名带 `_ch<渠道>`。
- **闪购**：复用 `shangou_report_download`，Playwright 连接**同一个** CDP；
  日期在页面上**实际设置后读回**再导出（`daily` 模式）；「无数据」是页面明确文案，作为证据。
  当前闪购**不支持**门店/渠道范围筛选：传了非 `all` 的 `scope_id` 会明确 `blocked(SCOPE_MISMATCH)`。
- 两平台统一收敛为 `collect_task(task, config) -> TaskResult`（`scripts/adapters.py`）。
- 依赖缺失（未装 playwright/websocket）→ `failed(PLATFORM_ERROR)`；
  CDP 端点不可达 → `blocked(BROWSER_UNAVAILABLE)`，提示先启动浏览器并登录。

> **页面定位现状（如实说明）**：定位规则目前内嵌在两个采集器里，**尚未外置**为
> `references/page_locators.json`。按 spec §6.2，只有**真实平台实测过**的定位才能定版；
> 本机离线阶段不外置、不编造选择器。T2 真实三轮采集通过后，再把已验证定位抽成
> 版本化配置，并按 `classify_change` 的规则做受控更新与回退。

## 五、登录检测与协助

`precheck_login(config)` 是**只读**预检：探测 CDP、定位两平台标签页、判断是否落在登录/验证页。
发现落在 `login / waimaie / passport / 验证码 / 安全验证` 一类页面即报 `LOGIN_REQUIRED`。

处置：**保存任务 → 给用户明确页面与操作提示 → 用户本人完成扫码/密码/验证码 →
重新预检 → 只继续未完成任务**（`resume`）。全程**不输出凭据、不自动清 Cookie**。

## 六、台账与命令

```bash
# 只查缺口（不采集）
python scripts/flux.py check-gap --today 2026-10-01 --config config.json

# 默认日期全量采集（台账里已完成的目标日任务自动跳过）
python scripts/flux.py collect --today 2026-10-01 --config config.json

# 定向补采某天（新建批次；未来营业日与不支持回溯的快照会被明确拒绝/列出）
python scripts/flux.py collect --today 2026-10-01 --date 2026-09-20 --config config.json

# 只采一张表
python scripts/flux.py collect --today 2026-10-01 --table 评价数据 --config config.json
```

`collect` 只写台账与批次清单，**绝不建库**。同一业务日期连续跑三次会用 `--batch-seq`
区分批次身份（同一日期同一模式默认是同批次，便于恢复）。
