# 制品位置

| 制品 | 唯一/当前位置 | 是否进 Git | 用户数据/分享边界 |
| --- | --- | --- | --- |
| Shared Core/App 源码 | `~/Documents/700-AI-Workspace/20-Projects/StockWatcher` | 是 | 唯一开发真源 |
| Mac App ZIP | `90-Archive/StockWatcher/00-current/app-mac/latest-app.zip` | 否 | 仅本机内部归档 |
| App provenance | `90-Archive/StockWatcher/00-current/app-mac/SOURCE_COMMIT.txt` | 否 | 可分享 fingerprint，不含凭据 |
| App/Main Bundle | `90-Archive/StockWatcher/00-current/app-mac/repository.bundle` | 否 | 恢复材料；不当作开发目录 |
| Web Handoff ZIP | `90-Archive/StockWatcher/00-current/web/latest-handoff.zip` | 否 | 不代表部署完成 |
| Web Bundle/worktree | `90-Archive/StockWatcher/00-current/web/` | Bundle 否；worktree 非主 Git | 保留独立 Web 线，不合 main |
| Web deploy | Web 独立 worktree 的 `deploy/` | 随 Web branch | `.env`/master key 不在包内 |
| Windows Handoff | `90-Archive/StockWatcher/00-current/windows/historical-handoffs/` | 否 | 历史材料，不能替代 Live |
| 真实日志/截图/PDF | `90-Archive/StockWatcher/20-live-evidence/` | 否 | 可能含用户环境信息，不公开分享 |
| 数据库/备份 | 本机 Application Support 与 archive history | 否 | 不移动、不删除、不分享 |
| 旧 Bundle/patch/workspace | `90-Archive/StockWatcher/30-backups/` | 否 | 可恢复；重复只列为候选 |
| 审计报告 | `90-Archive/StockWatcher/90-cleanup-reports/consolidation-20260807-2354/` | 否 | 仅治理证据 |

绝不把 Token、Keychain 导出、真实 `.env`、用户数据库、交易账户或完整运行日志放进 Git 或索引。
