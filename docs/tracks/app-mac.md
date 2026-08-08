# Desktop App / Mac 轨道

## Provenance

- 应用代码基线：`88ccf49f91fa814af83a004232315286feca3fb7`。
- 安装路径：`~/Applications/StockWatcher.app`。
- App 内 `Contents/Resources/stock_watcher/SOURCE_COMMIT`：`88ccf49f91fa814af83a004232315286feca3fb7`。
- 结论：App 与应用代码基线一致；收口治理提交不改变 App，未覆盖或重装 App。
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
- Windows 独立验收继续为 `FAIL`，不得由 Mac 结果覆盖。

## 归档与安全

- 当前 App ZIP：`90-Archive/StockWatcher/00-current/app-mac/latest-app.zip`。
- 当前 App Bundle：`90-Archive/StockWatcher/00-current/app-mac/repository.bundle`。
- `SOURCE_COMMIT.txt`、`SHA256SUMS.txt` 与 provenance 报告在同目录。
- 不清理 `~/Library/Application Support/StockWatcher`、`~/Library/Logs/StockWatcher`、macOS Keychain 或安装 App。
