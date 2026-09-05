# v0.7：Mac App 与 Web 共用版本

> 2026-09-05 · in_progress。用户要求 Mac App 更新并与 Web 同步。

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
- [ ] 同提交 Mac App 构建、签名检查、隔离 Replay 和实际图形界面验证。
- [ ] Web 同提交构建来源与部署候选准备，保持周末现网服务不启动。

## 交付边界

不合入 main，不 push，不启动现网 Docker 服务。不迁移生产数据库。
生产库升级须先提供副本演练结果、备份与回退对象，再按
`docs/process/boundaries.md` 的真实数据迁移要求确认。
Mac/回放/构建证据不解除 Web 的 `BLOCKED / NOT_ACCEPTED` 或 Windows 交易日验收。

## 进度

初始检查：本地 main 仍为 `6a81825`；现装 Mac 版本已从 Info.plist 和 SOURCE_COMMIT 实读。
Web 原工作区三项未提交部署/健康检查改动保持原位。

## 本地验证（2026-09-05，macOS arm64）

- 完整 pytest：587 passed，25 skipped，2 deselected。20 项需 Windows PowerShell，5 项原始交接 fixture 未入库；2 项 live_tushare 不执行。
- Ruff 全仓通过；Mypy 146 个源文件通过；workspace 29 项、Windows 离线打包契约、lock 与 diff 通过。
- 首次全量发现手动抓取重复保存，修复为复用同轮快照；CNB fixture 的 shell 缺 python，通过项目规定的 uv run 在锁定环境执行后通过。
- Mac 库副本从 v6 升至 v10，46 快照、138 明细、31 提醒、6 总结、24 任务、1135 轮扫描等原表行数全部一致；integrity=ok，FK 检查空。原库未改。
- 复盘/胜率/六笔日组合仍复用现有 domain/outcome_tracker；未修改候选算法和资金规则。
- 结构化自审：本轮差异未发现 P0/P1；Web 既有恢复、运维入口和备份问题仍按前一轮审核单保留，不因版本同步视为修复。

## 构建与切换

运行 `uv run --frozen python scripts/build_mac_web_release.py --mac --web --output <仓库外目录>`。
构建脚本要求干净提交，从同一 Git archive 构建两端，输出 release.json、Mac ZIP 和本地 Web 镜像。
Web 构建不会启动现网服务。真实切换时必须使用现网的三份 Compose 与原有 bind DB，不使用旧 named volume。
Mac 正式首启会将现用库从 Schema 6 升至 10，需先确认真实迁移；回退必须配对使用旧 App 与迁移前库。
