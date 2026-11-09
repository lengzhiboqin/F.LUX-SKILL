---
name: flux-weekly-goods-review
description: F.LUX 全球美妆集合店（华晨店）每周货盘复盘。用户每周一下载「新品数据-商品魔方2.0」「活动数据-营销分析」「货盘数据-牵牛花门店商品」三张表放进项目文件夹后，用本技能把三张表解析成 数据/week-<周>.json（数据层）、追加 index.json、同步看板内嵌快照，并做渲染自检后交付《商品周复盘看板》。触发词：每周货盘复盘、货盘周报、更新看板数据、新品活动货盘复盘、周一货盘、复盘会议报告。
---

# 每周货盘复盘（华晨店）

纯商品视角的周复盘：**上周上了哪些新品、做了哪些活动、效果如何、货盘结构往 2000 品标准款的偏离与收敛**。
架构固定为**数据层 + 视图层分离**：视图层 HTML 只写一次，每周只换数据。

```
每周货盘与新品复盘会议\
├─ 看板\商品周复盘看板.html      ← 视图层（六页签，只写一次；只改 embedded-data 块）
├─ 数据\week-2026Wxx.json        ← 数据层（每周一生成，本技能产物）
├─ 数据\index.json               ← 周索引（追加即可驱动周切换）
├─ 新品数据-商品魔方2.0\...      ← 源表：总览 csv（1 行）+ 明细 csv（多行）
├─ 活动数据-营销分析\...csv      ← 源表：单店营销活动类型
└─ 货盘数据-牵牛花门店商品\...xlsx ← 源表：门店商品（2233 行 × 88 列）
```

## 执行入口

工作目录固定为 `C:\Users\Administrator\Desktop\每周货盘与新品复盘会议`。
Windows 只有 PowerShell：一次调用用 `;` 串行，路径全部加双引号，python 用 `python -X utf8`。

```powershell
$sk="C:\Users\Administrator\AppData\Local\Doubao\User Data\Default\.doubao\agent_mode\workspace\.user_skills\flux-weekly-goods-review\scripts"
$root="C:\Users\Administrator\Desktop\每周货盘与新品复盘会议"
# 1) 解析三张表 → 数据\week-<周>.json + index.json（先 dry-run 看数字）
python -X utf8 "$sk\build_week.py" --root $root --dry-run
python -X utf8 "$sk\build_week.py" --root $root
# 2) 把当周 JSON 写回 HTML 内嵌快照（双击打开也能看）
python -X utf8 "$sk\sync_snapshot.py" --root $root
# 3) 渲染自检（18 个块必须全部非空）
& "C:\...\node\node.exe" "$sk\dom_check.js" "$root\看板\商品周复盘看板.html"
# 4) 截图自检（六页签，产物进临时目录，不进交付）
python -X utf8 "$sk\shoot_tabs.py" --root $root --out "C:\Users\Administrator\AppData\Local\Temp\flux_shot" --tag 9
```

node 路径：`C:\Users\Administrator\AppData\Local\Doubao\User Data\sandbox_runtime\bases\772984f6fd3a8ae0ae7d2422cd7c2bfa\node\node.exe`

## 每周六步（别跳步）

1. **确认三张表到位**：新品目录有「总览」和「明细」两份 csv；活动目录有当周 csv；货盘目录有最新 xlsx。缺任何一项 → 停下来问，不要拿旧文件凑。
2. **校周期**：脚本从文件名抓 `YYYYMMDD_YYYYMMDD` / `YYYYMMDD-YYYYMMDD`；新品与活动周期不一致时退出码 2，直接报给用户，不要猜哪张是对的。
3. **生成数据层**：`build_week.py` 写 `数据\week-<周>.json`（schema `flux-weekly-goods-v1`，19 个顶层键）并追加/更新 `index.json`。周编号用 ISO 周，如 `2026W45`。
4. **同步内嵌快照**：`sync_snapshot.py` 把同一份 JSON 写进 `<script id="embedded-data">`。数据层任何字段改动必须**两边同步**，不要只改一处。
5. **自检**：`dom_check.js` 要求 `rendered blocks: 18/18`、`errors: none`；`shoot_tabs.py` 六页签出图，逐页看一遍版式（条形起点齐平、无溢出、无空白断层）。
6. **交付**：`present_files` 交 `看板\商品周复盘看板.html` 与 `数据\week-<周>.json`（index.json 可选）。回复里只讲三句话结论 + 本周动作，不重述全部数字。

## 会停下来问的四种情况

1. **三张表不齐**：新品缺总览或明细、活动缺当周、货盘目录没有新 xlsx。
2. **周期对不上**：新品 20261102_20261108 与活动 20261109-20261115 混放。
3. **货盘表结构变了**：缺 `实物库存` / `上下线状态` / `美团闪购类目三级` 等关键列（脚本会 KeyError）。
4. **口径被打断**：新品明细出现无 `曝光人数-近7天)` 列、活动表缺「活动成本（元）」。
问法：一句话说明缺什么 + 影响哪一段，不要列长清单。

## 口径字典（每周必须一致，改了就要同步看板）

**货盘基础**
- 台账 SKU＝xlsx 行数；已上线＝`上下线状态=已上线`；在架＝`规格上下架状态` 含「美团：已上架」且 `是否手工下架` 含「美团：否」。
- 动销＝`月销量 > 0`（空值按 0 算）。**仓库默认 30 天口径**，字段就叫「近 30 天月销量」。
- 动销率＝动销 / 在架；零动销率＝(已上线且零动销) / 已上线。
- 库存一律用 `实物库存`，**禁用 `渠道库存`**（含 999999999 哨兵值，脚本已按 ≥1e8 置空）。
- 虚拟链接（`专属加急配送`/`单拍无效`/`运费`/`配送费补差`）不参与货值与滞销统计：`inventory_value` 含它，`inventory_value_excl_virtual` 与 `dead_value` 不含。
- 缺货＝**在架且实物库存=0**，分母同在架（`zero_stock_sku` / `stockout_rate`）。不要用已上线做分母。
- 滞销货值 `dead_value`＝已上线&零动销&库存>0 的 `采购价×实物库存` 之和，占比分母用 `inventory_value_excl_virtual`。
- 三级类目数取**已上线**口径；`category_l3_avg_sku` 为台账 SKU / 全部类目（密度口径）。

**七大类映射**（`grp()`）
- 美容护肤→skin；彩妆香水→makeup（二级=香水→fragrance）；美妆工具/服务→tool；
- 个人洗护：二级「美发护发」→hair，其余（身体清洁/身体护理/女士护理/男士洗护/旅行套装/口腔）→body；
- 其余全部→adjacent（女性连带）。七个 key 与 `category.key` 必须一致。

**价格带**：仅取 `美团闪购渠道售价` 非空的行，五档 `≤9.9 / 9.9–29.9 / 30–59 / 60–99 / ≥100`，标准占比 20–25/45–55/15–20/5–8/3–5。

**新品**：`count` 为明细行数（SKU 粒度），`exposure` 为明细曝光合计（**行级重复计数**），`exposure_uv` 取总览「新品曝光人数」去重值——两者不等属正常，报告必须注明。`orders/gmv/conversion/share_of_shelved` 一律取总览，不自己算。

**活动**：成本率＝`活动成本 / 活动营业总额`，**不是表里「投入产出比」列**（那列是成本率但口径混杂）。归因营业额多类型重叠（`overlap_ratio`＝归因合计/全店成交），**绝不可加总、不可当营收**。

**目标状态**：`ok` 区间内（允许 10% 相对容差）、`low` 低于下限、`high` 超上限、`review` 口径待核（毛利率）、`todo` 缺数据（客单价/连带率/库存周转）。

## 数据层字段（写 JSON 时按此顺序）

`schema / week / period{start,end,label} / store{name,code,city} / generated_at / headline{gmv,units,unit_price,orders,aov,new_products_gmv} / assortment{...} / category[7] / standard{total,skin_share,adjacent_share} / price_bands[5] / priced_sku / new_products{count,spu,share_of_shelved,exposure,exposure_uv,orders,gmv,conversion,all_conversion,zero_exposure_count,items[categories[rules]} / activities{...items,constraints} / quality{...channel_anomaly_detail} / targets[16] / actions[4]{step,title,now,act,target,why} / selection[3]{cat,dir,rule,basis} / gaps{count,items} / sources[4]`

`index.json`：`{schema, store, updated_at, weeks:[{week,label,start,end,file,summary{gmv,units,shelved,selling_rate,new_products_gmv}}]}`，同周重复执行按 week 覆盖，不重复追加。

## 视图层约束（别下周改坏）

- 六页签：概览 / 新品 / 活动 / 货盘结构 / 本周动作 / 目标差距；`location.hash` 切页签。
- 优先 `fetch("../数据/index.json")` → 周文件；`file://` 读不到时 600ms 回退到内嵌快照。
- **所有文案里的数字必须来自数据对象**，不允许写死；新增文案也遵守。已做动态化的位置：漏斗标题与副标题、新品标题计数与说明、活动洞察、价格带副标题与洞察、质量段、目标计数。
- 条形对齐：`barRow()` 用定宽名称列 + `sub` 上下两行 + `min-width:0` + ellipsis，行高 44px；新增行不得再插入独立列。
- 语义配色：颜色＝结论（红=低于标准/差、金=偏高/警示、绿=达标、深蓝与石板蓝为主色阶）。图例必须带数量。
- 改动样式只加在文件末尾的 CSS 覆盖块（v2/v3/v4 三层），不要改前三层原值。

## 常见陷阱

- CSV 必须 `encoding="gb18030"`，字段 `.str.strip()` 去掉尾部 `\t`。
- 活动表「日期」形如 `\t20261102_20261108`，活动类型带 `\t` 尾巴。
- 新品商品名是超长营销标题，`shorten()` 去前缀/切分隔符/切卖点尾巴后限 16 字，不要直接透传。
- `pandas Series.apply` 返回 None 会变 NaN；`value_counts()` 后判 isna 恒为 0。
- `pct()` 输出 `6.3%` 这类带一位小数，别写成 `6%` 丢精度。
- 无头 Chrome 必须带 `--virtual-time-budget=3000`，URL 加 `?v=N` 防缓存。
- HTML 内嵌快照与外部 JSON 曾漂移：改数据必须走 `sync_snapshot.py`，再用 `--check-only` 复核。
- 场景固定单店（华晨店 1157860 株洲），不做多门店切换；顶部周期选择器只切历史周。

## 基准（推导本周动作的依据）

2000 品标准款：护肤 550 / 彩妆 500 / 工具 220 / 美发 150 / 身体 180 / 香水 70 / 女性连带 330；价格带 450/1050/330/110/60；美妆核心 50–70%、动销≥50%、缺货≤5%、主图与详情 100%、品牌覆盖 100%（知名≥10%、新锐 30–40%、白牌≤15–25%）、客单 45–60 元、连带 2–3、周转 30–45 天、毛利 35–45%。
动作固定四条：补齐在架 → 砍零动销 → 补护肤减女性连带 → 提客单（30–59 元带 + 组合品）。
选品固定三条：护肤补缺口（只补 30–59 与 9.9–29.9 带）、女性连带减超配、新品每周 ≤10 款且全落核心七类。

## 交付话术

三段：
1. 本周商品面事实（成交/件单价/在架/动销率，一句）。
2. 新品与活动的共同问题（曝光与首单，一句）。
3. 本周动作（在架、砍品、品类、客单，四条内）。

不在回复里堆完整数字，明细全部由看板承载。
