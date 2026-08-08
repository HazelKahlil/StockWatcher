# Windows 后续轨道

## 当前状态

- 状态：`planned`。
- 本轮不恢复历史 Windows 副本为活跃开发目录，不创建 `windows/internal-test-v1`。
- 当前共享起点：已核验的本地 main `88ccf49f91fa814af83a004232315286feca3fb7`。
- 历史 Windows 结论继续为 `FAIL`：真实连接门/全市场扫描/真实 Top3 未通过；Mac 结果不能替代。

## 已保留材料

- 当前工程内：`packaging/windows/`、`scripts/windows/`、`docs/visions/v0.3-windows-data-gate/`、`docs/visions/v0.4-v1-feature-complete/`。
- 一页交接：`docs/visions/v0.3-windows-data-gate/windows-handoff.md`。
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

真正开始时从 main 创建临时 worktree/分支 `windows/internal-test-v1`，先回读本文件、活跃版本、数据/安全规则和 `requirements.lock.json`，再做平台改动。必须重新执行 Windows 目标机工程门和 Live 验收；Mac/CI 结果只作为跨平台静态证据。
