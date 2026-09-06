# v0.7：Mac App 与 Web 共用版本

> 2026-09-06 · Mac/Web 版本同步、正式切换和 Chrome 真实登录读回完成；交易日业务验收仍未完成。

以 Web `b97381a` 为代码起点，在 `codex/mac-web-version-sync` 隔离验证。
切换前 Mac 为 `0.4.0a1 / 88ccf49f`；Web 镜像为 `web-summary-fix-d84c3c7`，
包元数据为 `0.6.0a4`。Windows alpha.6 / `50904b6` 仅有 9 月 4 日交接记录，
本分支不包含该 Windows PR，也不更新 Windows 安装资产。

本次候选统一为 `0.7.0a1`（显示名 `0.7.0-alpha.1`）：Mac 与 Web 从同一个提交构建，
复用同一套排名、次日复盘、胜率、六笔日组合和重复出现规则。Mac 保留本地数据库与钥匙串；
Web 保留服务器数据库和登录。相同版本不承诺两个独立运行实例的历史数据相同。

## 范围与验证

- [x] 共用版本来源；Mac 包、Web 状态和写入记录不再各写版本常量。
- [x] Mac 接入 Web 已有重复出现提示与任务账本修复，不修改评分、提醒阈值或供应商。
- [x] 完整 pytest、Ruff、Mypy、workspace 与打包检查通过。
- [x] Mac 数据库副本升级演练，保留原库与原 App，核对迁移前后数据量。
- [x] 同提交 Mac App 构建、签名检查、隔离 Replay 和实际图形界面验证。
- [x] Web 同提交镜像构建与隔离浏览器登录、复盘验证；候选验证阶段未切换现网。
- [x] Owner 回复“继续”后，告知按保留已验证的 v10 库继续升级，并完成安装。
- [x] 正式 Mac 安装与 Web 切换后的配对版本读回。
- [x] Chrome 真实账号登录后的首页、复盘、总结和 09-04 PDF 已读回。

## 交付边界

不合入 main，不 push。初轮候选验证未切换现网；Owner 后续要求“继续”后，已完成
备份、Mac 安装与现有 Web/Worker 的同版本替换。周末无需自动启动 Docker 的偏好仍有效。
本轮存在一次非计划的本机库升级，不能继续声称生产库未变；详见下方事件记录。
Mac/回放/构建证据不解除 Web 的 `BLOCKED / NOT_ACCEPTED` 或 Windows 交易日验收。

## 进度

初始检查：本地 main 仍为 `6a81825`；现装 Mac 版本已从 Info.plist 和 SOURCE_COMMIT 实读。
Web 原工作区三项未提交部署/健康检查改动保持原位。

## 本地验证（2026-09-05，macOS arm64）

- 完整 pytest：587 passed，25 skipped，2 deselected。20 项需 Windows PowerShell，5 项原始交接 fixture 未入库；2 项 live_tushare 不执行。
- Ruff 全仓通过；Mypy 146 个源文件通过；workspace 29 项、Windows 离线打包契约、lock 与 diff 通过。
- 首次全量发现手动抓取重复保存，修复为复用同轮快照；CNB fixture 的 shell 缺 python，通过项目规定的 uv run 在锁定环境执行后通过。
- Mac 库副本从 v6 升至 v10，46 快照、138 明细、31 提醒、6 总结、24 任务、1135 轮扫描等原表行数全部一致；integrity=ok，FK 检查空。此步骤本身未修改原库。
- 复盘/胜率/六笔日组合仍复用现有 domain/outcome_tracker；未修改候选算法和资金规则。
- 结构化自审：本轮差异未发现 P0/P1；Web 既有恢复、运维入口和备份问题仍按前一轮审核单保留，不因版本同步视为修复。

## 构建与切换

运行 `uv run --frozen python scripts/build_mac_web_release.py --mac --web --output <仓库外目录>`。
构建脚本要求干净提交，从同一 Git archive 构建两端，输出 release.json、Mac ZIP 和本地 Web 镜像。
Web 构建不会启动现网服务。真实切换时必须使用现网的三份 Compose 与原有 bind DB，不使用旧 named volume。
Mac 从旧版升级涉及 Schema 6→10；回退必须配对使用旧 App 与迁移前库。

两端成品均来自产品提交 `8f34c32580460be4c7e81c2d58190c0726657ab1`，
后续本版本文档提交不改变该成品来源。仓库外交付根目录为 Archive 的
`99-deliveries/StockWatcher-0.7.0-alpha.1-20260905/`。

- Mac：`8f34c3258046/mac/StockWatcher.app`，arm64，ad-hoc 签名检查通过。
- ZIP：`8f34c3258046/StockWatcher-0.7.0-alpha.1-macOS-arm64.zip`。
- Web：`stockwatcher-web:mac-web-0.7.0-alpha.1-8f34c32`。
- 镜像 ID：`sha256:4efeaa8dbda7bd88bec254b37d851353f031d88decbd0657e192c8d4ea3f613a`。
- 配对来源与哈希：`8f34c3258046/release.json`；验证记录：`validation/validation.json`。
- 回退：`rollback/StockWatcher-0.4.0a1-before.zip`、`migration/before/stock-watcher.sqlite3`，
  原件哈希与具体绝对路径仅保留在仓库外 `rollback/rollback.json`。

## 实际界面验证（macOS，模拟数据）

打包 Mac App 以 `--provider replay --db <隔离库>` 和 `STOCKWATCHER_RUNTIME_ROOT=<隔离根>`
在持续存活的终端会话中运行；先核对 PID、启动参数和日志目录，再由 CUA 选择该 App。
主界面显示三个带 rank 的模拟候选与 `0.7.0-alpha.1`；历史→次日复盘可打开。
通过菜单正常退出，startup 记录为 `exit_code=0 / normal-exit`。

同一份六笔模拟复盘记录复制给新 Web 镜像，仅挂载模拟目录并将端口绑定本机回环。
以临时模拟账号在浏览器登录，打开次日复盘；两端均显示 33.3% 胜率、+0.79% 平均收益、
100% 日组合胜率、6/6 已结算、一个完整组合日，上午/下午平均为 +0.76% / +0.82%。
`/health/version` 返回 `0.7.0-alpha.1` 和上述完整提交。
临时 Web 容器与浏览器页已关闭；未运行行情 Worker。

截图和 AX/DOM 证据位于本任务的 CUA 工具记录，未声称仓库中存在独立截图文件。
这些是模拟功能与打包证据，不证明真实交易日扫描、结算或 Windows 验收。

## 验证工具误启动事件（已保留升级库并完成安装）

首次通过短命令派生进程启动 App 后，隔离进程未保持运行；CUA 选择 App 时自动启动了
一个未带 Replay 参数、未带隔离环境的候选实例。该实例进入默认 Tushare 初始化，
将本机 App 库从 Schema 6 升至 10。后来一次状态读取也自动重启了已退出的目标。
当时已停止这些进程，并保留启动前完整库、升级后副本和逐行对比；当时正式安装路径中的旧 App 未替换。

- 候选快照、候选明细、提醒、盘后总结、任务和扫描的原有各字段逐行保留，无新增真实扫描。
- 新增重复出现索引和两条 app settings；新增运行审计；一条旧 runtime session 被补记
  `unclean_exit`，不是候选历史丢失。前后库完整性均 `ok`，外键错误均为 0。
- Owner 随后回复“继续”。执行前已明确说明按保留已验证的 Schema 10 库继续正式升级，
  完成 Mac 安装；未执行旧库恢复。升级前 v6 库与本次切换前 v10 库均保留。
- 对比与具体路径仅在仓库外 `incident-validation/`，不提交数据库或运行日志。
- 后续隔离验证改用持续终端会话，读回 PID/argv 后才交给 CUA；退出后只检查进程和启动日志，
  避免状态读取再次自动启动默认实例。

## 现网只读快照（2026-09-05 约 21:58）

Docker 现网容器当时已运行约九小时，Web 为旧镜像 `web-summary-fix-d84c3c7`，
状态 unhealthy；本机与公网 readiness 均为 503。活库最新快照仍为 7862，
时间 `2026-09-04T15:00:06.806420+08:00`。这与早先“Docker 已关闭”描述不同，
截至该次只读检查，尚未启动、重启、替换现网容器，也未运行活库 integrity_check。
这份快照不能用于宣称服务恢复；周末无需为了可用性自动拉起服务的用户偏好继续有效。

## 正式切换与复核（2026-09-05 晚至 09-06 08:35，macOS）

Mac 正式安装位置 `~/Applications/StockWatcher.app` 已替换为 `0.7.0-alpha.1`，
SOURCE_COMMIT 为 `8f34c32580460be4c7e81c2d58190c0726657ab1`。
签名检查与可执行文件哈希匹配配对构建清单。安装后的 App 用隔离 Replay 实际打开主窗与
次日复盘，再由菜单正常退出（`exit_code=0 / normal-exit`）；未以此冒充 Mac 真实行情验收。

Web 与 Worker 均使用 `stockwatcher-web:mac-web-0.7.0-alpha.1-8f34c32`。
现网仍用三份 Compose 和原 bind 数据目录，仅更新 `.env.tunnel` 中版本相关三项；
轻量健康检查已在镜像中，移除了 Worker 的临时源码挂载。运维配置记录见现网 worktree 的
`deploy/release-current.json`；构建源码来自本同步分支，不能拿旧运维 worktree 代码冒充新镜像来源。

切换前先停止 Web/Worker，通过 SQLite backup API 保存完整一致副本，只对仓库外副本
运行完整性与外键校验：Web 库 Schema 10、7777 快照、23323 明细、88 提醒、17 总结、
96 复盘记录、6 账号，最大快照仍为 7862 / 2026-09-04 收盘；integrity=ok，外键错误 0。
合格副本已写入 `/backups/cutover-20260905T142230Z/stockwatcher.db`，并校验复制前后哈希。
这是目前优先于 09-03 恢复点的切换备份，不删除旧备份。

新 Web 本机 ready 200 后，针对公网 1033 只重启 cloudflared，公网恢复。
09-06 08:35 复核：Web/Worker 持续运行约十小时且均 healthy，本机与公网 readiness 均 200。
公开版本接口已读回同一完整提交。只读投影可提供快照 7862 的三个 rank（1/2/3），来源明确是
09-04 的上次实时快照；当前 warming 不作为周末新候选或盘中验收证据。

账号密码校验字段和权限与切换前逐项相同。公网登录页显示新版本；初次验证误将交接中的
两个独立账号视为用户名/密码组合，被正常拒绝。随后 Owner 明确已在 Chrome 登录，已实际打开首页、次日复盘、
盘后总结和 09-04 PDF；未修改或重置账号。生产登录与页面可访问性已经读回，数据口径和
真实交易日验收仍分别保留限制。

仓库外切换证据位于本交付根的 `cutover-20260905T142230Z/`，含 `cutover.json`、
Mac 旧 App、两端切换前库、数据量与完整性检查及旧版本配置值；不提交数据库、凭证或日志。
原先四项 P1 运维/恢复问题、历史耗尽重试与 `secret-prune` 外键告警仍属独立待办。
本次版本同步完成不解除 Web `BLOCKED / NOT_ACCEPTED` 和 Windows 的独立验收门。

## Chrome 真实登录读回与报告恢复（2026-09-06，macOS）

Owner 完成登录后，使用现有 Chrome 会话实际检查以下页面；首页、复盘和总结页的页脚均显示 `0.7.0-alpha.1`：

- 首页：三个带 rank 的观察项。活库只读查询仍是快照 7862、09-04 收盘；09-04 有 618 个快照，09-05/06 无新候选快照。
- 次日复盘：96 笔记录，75 已结算、21 pending；显示胜率 29.3%、日组合胜率 27.3%、平均收益 -1.53%，11 个完整组合日。数据表状态计数与页面一致。
- 盘后总结：最新记录为 09-04 15:30:05。首次点 PDF 返回“PDF 不存在”；文件实际位于 bind 库旁 `db/reports`，下载接口读取独立 reports 目录。
- 报告恢复：只复制 09-01 至 09-04 的 16 个现存 PDF/JSON/Markdown/metadata 文件到下载目录，逐个验证 SHA256，没有覆盖冲突文件或重新生成内容；同样 16 个文件已进入本次切换备份的 `reports/`。随后 Chrome 完整打开 09-04 两页 PDF，保留原报告日期、来源和旧构建提交。

公网与本机 readiness、公开版本接口均读回 200；Web/Worker 使用同一镜像、healthy、重启计数 0。
一次系统 Python urllib 公网探针返回 403，随后按现网 curl 路径读回 ready/version 均 200，真实 Chrome 页面持续可访问；未据此重启服务。

本次读回发现的显示问题（后续负责人：Codex，独立返修，尚未实施）：

1. 首页“最后更新时间”显示 09-05 14:59:12 的最后扫描完成时间，候选却是 09-04 的保留快照。`server/static/dashboard.js:315` 优先使用 `last_scan.completed_at`；需要分别标示扫描与候选数据时间。
2. 复盘回补提示写“96笔未纳入统计”，但当前 75 笔已结算。`server/api.py:_backfill_payload` 将历史回补的 skipped/unavailable 汇总当作排除统计数量，文案与当前账本范围混淆；不得据此更改实际胜率分母。

报告文件复制只恢复现存 09-01 至 09-04 下载，不修复 Worker/下载/备份目录不一致的代码根因，也不证明更早日期 PDF 已恢复。
四项 P1 的完整修复仍按 `v0.7-web-candidate-repeat/operational-audit-20260905.md` 分配给后续 Codex 返修；轻量 healthcheck 固化和最新完整库备份是本次已落实的局部改善。

本版本产品源码仍固定为 `8f34c32`；收尾提交仅更新文档和部署清单。主线 `6a81825` 未合入，GitHub 未同步。
现网运维 worktree 原有 `deploy/docker-compose.yml`（healthcheck 20s）和 `src/stock_watcher/server/healthcheck.py`（轻量探测）仍未提交、原样保留；bind overlay 和发布清单由本次运维提交保存。
截图与 AX 证据见任务工具记录；仓库外 `web/browser-readback.json`、`web/report-download-recovery.json`、`web/report-backup.json` 记录本次读回和文件哈希。
