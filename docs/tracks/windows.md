# Windows 后续轨道

## 当前状态

- 状态：`handoff_ready / WINDOWS_SMOKE_PASS`；权威连续 30 分钟 M0 仍待脱敏证据。
- Windows 同步基线：远端 `main@6b7936f795fb93e0babebb7bd70b6c767cadd83e`。
- 当前回传分支：`publish/v0.3.1-windows-smoke`。
- Human Owner 已报告交易日实际使用基本可用，但仓库内尚无连续 30 分钟、完整交易日、提醒和安装器验收指标，因此不得写成权威 M0 `PASS`。
- Tushare 是正常数据路线；TdxQuant/TdxW 只保留可选诊断，不是启动或预检前提。

## 已保留材料

- 当前工程内：`packaging/windows/`、`scripts/windows/`、`docs/visions/v0.3-windows-data-gate/`、`docs/visions/v0.4-v1-feature-complete/`。
- 一页交接：`docs/visions/v0.3-windows-data-gate/windows-handoff.md`。
- 当前 Windows Tushare 回传：`docs/visions/v0.3.1-windows-tushare-data-gate/windows-20260811-handoff.md`。
- 历史 Handoff：`90-Archive/StockWatcher/00-current/windows/historical-handoffs/`。
- 其余旧 worktree/解压恢复树在 `90-Archive/StockWatcher/10-history/windows/` 或 `30-backups/retired-workspaces/`，不作为开发真源。

## 未来范围

只做平台适配和 Windows 独立验收：

- Windows Credential Manager；
- Windows 路径、通知、多屏、单实例、自动启动；
- PyInstaller/portable/Inno Setup、签名、安装/卸载/回滚；
- 目标 Windows 的真实数据源、交易时段、恢复和 Live 验收。

Shared Core 的 Provider 归一化、候选算法、StableTop3、调度和 SQLite 语义不得复制或另行改写。

## 开工门

下一步先评审并合并 `publish/v0.3.1-windows-smoke`，再从最新 `main` fresh clone 重做
Windows 工程门和交易时段验收。Mac/CI 结果不能替代 Windows 实机证据，Human Owner 的
“基本可用”回报也不能替代脱敏的连续 30 分钟 M0 指标。
