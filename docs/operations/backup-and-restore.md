# 备份与恢复

## 当前恢复点

- 收口前完整 Bundle：`90-Archive/StockWatcher/90-cleanup-reports/consolidation-20260807-2354/StockWatcher-before-consolidation.bundle`。
- 收口前 Bundle SHA-256：`e323adf40c4661073a963ecf5ad223fa6714c3f374665a18dab2fe0cb8f86fa9`。
- 收口前 `git bundle verify`：通过，包含完整 reachable history。
- 当前 App/Main Bundle：`90-Archive/StockWatcher/00-current/app-mac/repository.bundle`。
- 当前 Web Bundle：`90-Archive/StockWatcher/00-current/web/repository.bundle`。

## 恢复原则

1. 先复制 Bundle 到临时恢复目录，再 `git bundle verify`。
2. 恢复源码时创建新的临时 clone/worktree；不要覆盖当前 main，不要 reset/clean。
3. 先核对 Commit、tag、工作树状态和 SHA-256，再运行窄范围工程门。
4. App 恢复只处理归档副本；不覆盖 `~/Applications/StockWatcher.app`，不触碰 Keychain、Application Support、Logs 或用户数据库。
5. 数据库恢复必须使用应用现有 backup/restore/迁移流程；本轮不执行真实数据库迁移和历史删除。
6. Web 恢复保留 branch/worktree 独立性；未通过 VPS/Live 门前不得绑定域名或合入 main。

禁止：`git reset --hard`、`git clean -fdx`、`git branch -D`、force push、删除 Keychain/用户数据库或覆盖安装 App。
