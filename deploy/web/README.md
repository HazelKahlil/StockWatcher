# Web deploy 边界

本文件是 main 的治理入口，不复制 Web 部署资产。Web 尚未 accepted，真实部署源码唯一保留在独立分支 `web/internal-test-v1` 的 worktree：

`~/Documents/700-AI-Workspace/90-Archive/StockWatcher/00-current/web/source-worktree-87a8b85609f57504861e09f416694582556b736e/deploy/`

在 Web 通过完整工程门、VPS preflight、备份恢复和完整交易日验收前，不把这套资产复制到 main、不绑定域名、不生成真实 `.env`、不宣称上线。
