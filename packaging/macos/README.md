# macOS packaging 入口

macOS spec 当前仍位于 `packaging/stockwatcher-macos.spec`，本目录只作为平台入口索引；不搬迁 spec，避免改变构建、测试或历史路径。

构建、签名和安装证据见 `docs/tracks/app-mac.md` 与 `90-Archive/StockWatcher/00-current/app-mac/`。App 只生成本机 arm64 内部测试包，Token、SQLite、缓存、报告和日志不得进入包。
