# Web 内部测试轨道

## 唯一事实源

- 分支：`web/internal-test-v1`。
- HEAD：`bf447ba62957e3d12a766df26c980b96ad4c74b2`。
- Web 基线：`502a447d7e593d638ea45518f2a5e4d4827f683f`（Mac RC3 tag 基线）。
- Web 与 main 的共同基线是 `502a447...`；Web 有独有实现提交，不能把它当作 main 的祖先。
- 当前 worktree：`~/Documents/700-AI-Workspace/90-Archive/StockWatcher/00-current/web/source-worktree-87a8b85609f57504861e09f416694582556b736e`。
- 当前 Bundle：`90-Archive/StockWatcher/00-current/web/repository.bundle`。
- 当前 Handoff：`90-Archive/StockWatcher/00-current/web/latest-handoff.zip`。

## 状态

**`BLOCKED / NOT_ACCEPTED`。不得称为已接受、生产稳定或已合入 main。**

原因：

1. 当前 macOS worktree 的完整 pytest 为 `434 passed, 25 skipped, 2 deselected`；Ruff、
   Mypy（136 source files）、workspace validator（29 个必需文件）和 JavaScript syntax check
   全部通过。
2. Web、唯一 Worker、Caddy/Tunnel 当前在 Mac Docker Desktop 运行；有 healthcheck 的容器
   为 healthy，`https://stock.hazelkahlil.com/` 当前返回 HTTP 200。
3. 以上仍是 Mac 主机与当前公网入口证据，不等于完整交易日、持续运行、提醒重放、备份恢复
   和浏览器完全关闭后的通知验收。
4. 当前部署依赖 Mac 开机、联网及 Docker Desktop；VPS 已由 Human Owner 明确后置，本基准
   不把 Mac 托管写成独立服务器部署。

## 已实现范围

- FastAPI/Jinja2/原生 CSS/ES modules/WebSocket。
- 唯一 headless Worker；lease 防重复 Worker；Web 不复制 CandidateEngine、StableTop3 或自动调度。
- REST/API、Admin/Tester、Argon2id session、CSRF、RBAC、AES-256-GCM Token 存储边界和全局 redaction。
- SQLite WAL v7、备份/恢复 CLI、Compose/Caddy 及运维脚本。

## 部署资产边界

- Web branch 的实际部署源码位于其独立 worktree 的 `deploy/`。
- main 不复制 Web server、worker 或部署实现；main 的 `deploy/web/README.md` 仅是边界索引，不是第二套资产。
- 当前 `stock.hazelkahlil.com` 由 Mac Docker + Cloudflare Tunnel 提供；本次基准只做只读
  复核，没有修改域名、Tunnel、账号或部署配置。
- 真实 `.env`、Token 和 master key 不进入 main、基准文档、日志或打包制品。

## 下一步

1. 继续补完整交易日、强异动通知、断线恢复、事件重放和备份恢复验收。
2. 浏览器完全关闭后的 Web Push 如仍需要，应作为独立功能实现；当前只有页面内提醒与浏览器
   Notification。
3. 全部门通过后，由 Human Owner 决定是否建立受控集成提交；在此之前保持
   branch/worktree/Bundle 完整并继续 `BLOCKED / NOT_ACCEPTED`。
