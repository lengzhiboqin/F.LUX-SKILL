# 安装与配置

适用宿主：**Windows** 与 **Linux**。正式交付以两端实测为准。

## 一、宿主前提

| 组件 | 要求 | 说明 |
|---|---|---|
| Python | 3.11+（本机实测 3.13.14） | 标准库 `csv/sqlite3/argparse/...` 即可 |
| Node.js | **24+（必须）** | 只读查询用内置 `node:sqlite`（本机实测 v24.16.0），无需 npm 装包 |
| 浏览器 | Chromium 内核 + CDP 调试端口 | 牵牛花/闪购都连**已登录**的浏览器，不复用本机用户数据目录做自动登录 |

## 二、安装

```bash
# 1) 解压技能包到任意目录（路径含空格/中文均可，路径按 config.json 所在目录解析）
unzip 2026-10-02_F.LUX技能包_v0.2.zip -d ./flux-daily-reports
cd flux-daily-reports

# 2) 建隔离虚拟环境并装依赖（不要装到全局）
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python -m playwright install chromium      # 仅闪购采集需要

# 3) 配置：复制样例后按宿主修改
cp references/config.example.json config.json
```

`config.json` 里**所有相对路径都相对 config.json 所在目录解析**，因此从别的
工作目录调用也能定位到正确文件。**不要把账号、Cookie、真实门店主档写进技能包。**

## 三、浏览器准备（采集前必做）

1. 用**独立**用户数据目录启动一个带远程调试端口的 Chromium：

   ```bash
   # Windows
   "C:\Program Files\Google\Chrome\Application\chrome.exe" ^
     --remote-debugging-port=9222 --user-data-dir="%LOCALAPPDATA%\flux-cdp-profile"
   # Linux
   google-chrome --remote-debugging-port=9222 --user-data-dir="$HOME/.flux-cdp-profile"
   ```

2. 在这个窗口里**手动登录**牵牛花与闪购后台，保持窗口开着。
3. 确认端口可达：`python scripts/flux.py check-gap --config config.json`
   （配置错误会明确报错，不会静默通过）。采集时若提示 `LOGIN_REQUIRED` 或
   `BROWSER_UNAVAILABLE`，按 `references/recovery.md` 处理。

端口/主机在 `config.json` 的 `cdp_host` / `cdp_port`。

### 云端宿主（Linux 云窗口）

云端与本地**同一套链路**，差别只在「谁来起浏览器」和「谁来登录」。

```bash
bash scripts/cloud_bootstrap.sh          # 装依赖 + 起带 CDP 9222 的 Chromium
bash scripts/cloud_bootstrap.sh --browser # 依赖已装，只重启浏览器
bash scripts/cloud_stage1.sh --plan-only # 登录后：只看缺口，先不采集
bash scripts/cloud_stage1.sh             # 登录后：跑完整链路（缺口→采集→入库→自检）
```

`cloud_bootstrap.sh` 做四件事：校验 Node ≥ 24 → 建 venv 装依赖 → 装 Playwright
Chromium → 起 Chromium（`--remote-debugging-port=9222`，profile 存
`~/.flux-chrome-profile`）。

`cloud_stage1.sh` 会先探活两个平台 CDP，再 `check-gap` 列出待采清单，最后
`flux run` 跑完整链路；任一步失败即停，不会带着半截数据往下走。
缺口无需手填：`run_daily` 按「库里最后一天 + 1 → 今天 − 1」自动规划。

**登录态与浏览器的关系（重要）**：

| 事项 | 说明 |
|---|---|
| 登录态存放 | `$FLUX_PROFILE_DIR`（默认 `~/.flux-chrome-profile`），**进程重启不丢** |
| 会丢的场景 | 云会话被回收 / profile 目录被清 → 需重新扫码登录 |
| 你做什么 | 用 VNC/远程桌面打开云端浏览器，人工登录牵牛花 + 闪购 |
| 我做什么 | 起浏览器、连 CDP、跑采集/清洗/入库/自检 |

CDP 端点用环境变量指定，两侧统一：

```bash
export QIANNIU_CDP=http://127.0.0.1:9222   # 牵牛花（cdp.py）
export SHANGOU_CDP=http://127.0.0.1:9222   # 闪购（shangou_report_download.py）
```

未设置时：牵牛花默认 `127.0.0.1:9222`；闪购按 `Windows→9333,9223,9222` /
`Linux→9223,9222` 顺序探活（Windows 下 9222 常被占用，故避开）。

**远端 CDP**（浏览器不在本机时）：`QIANNIU_CDP` / `SHANGOU_CDP` 支持完整
`http://host:port`，可指向隧道/另一台机器，例如
`export SHANGOU_CDP=http://127.0.0.1:9223`（SSH 端口转发到云端 9222）。
闪购侧因此改为「端口数组 + 逐个探活」，配错时给出 `BROWSER_UNAVAILABLE` 而非静默失败。

**云端注意**：异地 IP 登录美团系后台可能触发验证码/二次验证，属平台风控，
须人工完成；技能包**不做**任何绕过。若平台拒绝异地登录，退回「本机采集 →
原件上传 → 云端清洗入库」的分离模式。

### 无原生桌面的云端容器：VNC 栈（v0.2 实机验证）

容器类云端宿主通常**没有远程桌面**，无法直接打开浏览器人工登录。v0.2 内置一套
自建图形栈，已在腾讯云北京容器实机跑通三轮：

```bash
bash scripts/cloud_vnc_daemon.sh        # 一键起全套：Xvfb(:99) + x11vnc + noVNC(3000) + Chrome CDP(9222)
```

| 组件 | 端口 | 作用 |
|---|---|---|
| Xvfb `:99` | — | 虚拟显示（1280×800） |
| x11vnc | 5900 | 把 `:99` 转成 VNC |
| websockify / noVNC | **3000** | 浏览器里打开 `http://<宿主>:3000/vnc.html` 即可人工登录 |
| Chromium CDP | 9222 | 技能包采集用 |

用法：先起栈 → 用浏览器打开 noVNC 页面人工登录牵牛花与闪购 → 再跑
`cloud_stage1.sh`。登录态存在 `~/.flux-chrome-profile`，**栈重启不丢**。

**已知坑：Chrome 单例锁残留。** 容器休眠/被回收后旧进程被杀，profile 里会留下
`SingletonLock` / `SingletonCookie` / `SingletonSocket`，导致再次启动报
「profile appears to be in use」。清锁即可，**不要动 Cookies**（登录态在里面）：

```bash
rm -f ~/.flux-chrome-profile/SingletonLock \
      ~/.flux-chrome-profile/SingletonCookie \
      ~/.flux-chrome-profile/SingletonSocket
bash scripts/cloud_vnc_daemon.sh        # 重启，登录态仍在
```

## 四、首次运行前的最小自检（不连平台）

```bash
python -m unittest discover -s tests -v     # 全部离线用例，不需要浏览器
python scripts/flux.py check-gap --today 2026-10-01 --config config.json
node scripts/query.mjs help                 # 只读查询可用性
```

离线用例全绿 + `check-gap` 能列出 19 表缺口，即说明安装与配置正确。
**这些只证明环境就绪，不能替代真实平台验收。**

## 五、目录约定（运行后自动生成，均已加入打包白名单之外）

```
<安装目录>/
├── SKILL.md
├── requirements.txt
├── config.json                 # 你的配置（不打包）
├── scripts/                    # 代码
├── references/                 # 契约、规则、字典、页面定位（定位配置待真实平台实测后外置）
├── tests/                      # 离线回归
└── _data/                      # 运行数据（不打包）
    ├── _ledger.json            # 任务台账：已验收/无事件状态
    ├── _batches/<batch_id>/    # 每批清单 manifest.json
    ├── raw/<batch_id>/         # 逐批留存的原件
    ├── _backup/                # 保留期维护的一致性备份
    └── flux.db                 # 唯一业务库
```

## 六、升级与回退

- 升级：解压新包到**新目录**，把旧目录的 `config.json` 与 `_data/` 复制过去；
  先跑离线用例，再跑一次 `check-gap`。
- 回退：切回旧目录即可，`_data/flux.db` 是完整可恢复的业务库。
- `references/cleaning_rules.json` 带 `rule_version`；入库时版本随批次写入
  `_batch`，事后可追溯某批数据用的哪版规则。
