# v0.7：Mac App 与 Web 共用版本

> 2026-09-05 · in_progress。Mac/Web 同提交候选已构建并验证；正式切换未完成。

以 Web `b97381a` 为代码起点，在 `codex/mac-web-version-sync` 隔离验证。
现装 Mac 为 `0.4.0a1 / 88ccf49f`；Web 镜像为 `web-summary-fix-d84c3c7`，
包元数据仍为 `0.6.0a4`。Windows alpha.6 / `50904b6` 仅有 9 月 4 日交接记录，
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
- [x] Web 同提交镜像构建与隔离浏览器登录、复盘验证；未启动或切换现网容器。
- [ ] Owner 决定验证误触的 Mac 本机库保留或恢复，完成相应处理。
- [ ] 正式 Mac 安装与 Web 切换后的配对版本读回。

## 交付边界

不合入 main，不 push，不启动现网 Docker 服务。计划的生产迁移必须先提供副本演练、
备份与回退对象，再按 `docs/process/boundaries.md` 确认。
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

## 验证工具误启动事件（待处理）

首次通过短命令派生进程启动 App 后，隔离进程未保持运行；CUA 选择 App 时自动启动了
一个未带 Replay 参数、未带隔离环境的候选实例。该实例进入默认 Tushare 初始化，
将本机 App 库从 Schema 6 升至 10。后来一次状态读取也自动重启了已退出的目标。
已停止这些进程，并保留启动前完整库、升级后副本和逐行对比；正式安装路径中的旧 App 未替换。

- 候选快照、候选明细、提醒、盘后总结、任务和扫描的原有各字段逐行保留，无新增真实扫描。
- 新增重复出现索引和两条 app settings；新增运行审计；一条旧 runtime session 被补记
  `unclean_exit`，不是候选历史丢失。前后库完整性均 `ok`，外键错误均为 0。
- 原库内容已变化；是否恢复到验证前完整备份或保留升级后的库，已向 Owner 发出具体选择，
  尚未收到回复。未经选择不执行恢复、再迁移或正式 App 安装。
- 对比与具体路径仅在仓库外 `incident-validation/`，不提交数据库或运行日志。
- 后续隔离验证改用持续终端会话，读回 PID/argv 后才交给 CUA；退出后只检查进程和启动日志，
  避免状态读取再次自动启动默认实例。

## 现网只读快照（2026-09-05 约 21:58）

Docker 现网容器当时已运行约九小时，Web 为旧镜像 `web-summary-fix-d84c3c7`，
状态 unhealthy；本机与公网 readiness 均为 503。活库最新快照仍为 7862，
时间 `2026-09-04T15:00:06.806420+08:00`。这与早先“Docker 已关闭”描述不同，
本轮未启动、重启、替换现网容器，也未运行活库 integrity_check。
这份快照不能用于宣称服务恢复；周末无需为了可用性自动拉起服务的用户偏好继续有效。
