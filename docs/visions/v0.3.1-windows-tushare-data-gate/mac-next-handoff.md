# Mac 后续交接

Mac 阶段只能从 Windows 完成后的同一 commit 开始。

必须复用 `providers/tushare/`、domain、候选/提醒算法、板块算法、SQLite、配置模型和核心
PySide6 UI。Mac 只适配 Keychain、通知中心、Login Items/LaunchAgent、路径、窗口和
APP/DMG/PKG。不得重新开发一套 Mac 筛选算法，也不得用 Mac Replay 替代真实 M0。
