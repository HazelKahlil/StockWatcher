# Desktop App / Mac 轨道

## Provenance

- 当前可重建源码基线：`ad04e392158c7050f84e0318fe1d53aaa0370c34`（`0.4.0a2`）。
- 安装路径：`~/Applications/StockWatcher.app`。
- App 内 `Contents/Resources/stock_watcher/SOURCE_COMMIT`：`88ccf49f91fa814af83a004232315286feca3fb7`。
- 结论：现有 App 仍是已验证的内部试用资产，但早于 alpha.2 源码基线；本轮没有覆盖、重装
  或读取其 Keychain/运行数据。需要分发 alpha.2 时必须从 tag 重建并重新记录 provenance。
- 可执行文件：arm64 Mach-O；SHA-256 `b601b4584867f4c5acef5a6f164e4b0ef458e2ef7416cfd598e93a0a667552ed`。
- 签名：ad-hoc；`codesign --verify --deep --strict` 返回 0；不是 Developer ID/公证发布。
- 当前 App 归档：`~/Documents/700-AI-Workspace/90-Archive/StockWatcher/00-current/app-mac/`。

## 已核验能力（macOS 本机）

- 原生实时 1/100/300/800 与全市场扫描；行业/概念板块、板块硬门、稳定 Top3、候选池强异动。
- 09:45/14:45 调度、30 天提醒历史、15:30 盘后总结与固定三页 PDF。
- macOS Keychain、SQLite 历史、单实例、窗口恢复、关闭隐藏、Dock/Finder 激活和显式退出。
- arm64 App 构建、ad-hoc 签名、安装版读取既有用户数据的验收。

## 未完成的 Live 门

- 新鲜 09:45/14:45 固定 Top3。
- 交易日 15:30 准点总结。
- 无旧缓存冷启动、真实睡眠/唤醒与断网/网络恢复图形会话。
- Windows 已达到 `WINDOWS_SMOKE_PASS`，但权威 M0 和完整现场验收仍未完成；不得由 Mac
  结果覆盖。

## 当前源码门

- pytest：`363 passed, 20 skipped, 2 deselected`。
- Ruff、Mypy、workspace validator、lock check、Windows package contract 与
  `git diff --check` 全部通过。
- 这些是源码与 macOS 工程证据，不表示已安装 App 已重建为 `0.4.0a2`。

## 归档与安全

- 当前 App ZIP：`90-Archive/StockWatcher/00-current/app-mac/latest-app.zip`。
- 当前 App Bundle：`90-Archive/StockWatcher/00-current/app-mac/repository.bundle`。
- `SOURCE_COMMIT.txt`、`SHA256SUMS.txt` 与 provenance 报告在同目录。
- 不清理 `~/Library/Application Support/StockWatcher`、`~/Library/Logs/StockWatcher`、macOS Keychain 或安装 App。
