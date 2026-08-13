# Web 内部测试轨道

## 唯一事实源

- 当前本地 Web 主线：`cnb/main`；实现提交 `53501ad` 已由返修分支
  `fix/web-desktop-ui-alert-lifecycle` 快进合入。
- 当前代码未部署；上一轮已验证的 Mac Docker 实现提交仍为
  `cfc6cd66be3edcf5468840c29b509eae643896b5`。
- Web 基线：`502a447d7e593d638ea45518f2a5e4d4827f683f`（Mac RC3 tag 基线）。
- Web 与 main 的共同基线是 `502a447...`；Web 有独有实现提交，不能把它当作 main 的祖先。
- 当前 worktree：`~/Documents/700-AI-Workspace/90-Archive/StockWatcher/00-current/web/source-worktree-87a8b85609f57504861e09f416694582556b736e`。
- 当前 Bundle：`90-Archive/StockWatcher/00-current/web/repository.bundle`。
- 当前 Handoff：`90-Archive/StockWatcher/00-current/web/latest-handoff.zip`。

## 状态

**`BLOCKED / NOT_ACCEPTED`。不得称为已接受、生产稳定或已合入 main。**

原因：

1. 当前 macOS worktree 的完整 pytest 为 `509 passed, 26 skipped`；定向 Web 契约为
   `35 passed`，完整门在 `-W error` 下零告警。Ruff、Mypy（142 source files）、workspace validator
   （29 个必需文件）、JavaScript syntax check 全部通过，生产与全锁定依赖
   审计沿用上一轮无已知漏洞证据。
2. Web、唯一 Worker、Caddy/Tunnel 当前在 Mac Docker Desktop 运行；镜像
   `stockwatcher-web:web-alpha4-cfc6cd6`、Schema v9、公网 `app.css?v=13`、部署前后备份与
   公网入口均已验证。
3. 当前桌面视觉与提醒生命周期结构化代码 Review 为 `P0=0 / P1=0 / P2=0`。以上仍是
   Mac 本地代码与浏览器证据，不等于部署完成，也不等于真实交易日的固定提醒、同点结算/
   重试和全日运行验收。
4. 当前部署依赖 Mac 开机、联网及 Docker Desktop；VPS 已由 Human Owner 明确后置，本基准
   不把 Mac 托管写成独立服务器部署。

## 已实现范围

- FastAPI/Jinja2/原生 CSS/ES modules/WebSocket。
- 唯一 headless Worker；lease 防重复 Worker；Web 不复制 CandidateEngine、StableTop3 或自动调度。
- REST/API、Admin/Tester、Argon2id session、CSRF、RBAC、AES-256-GCM Token 存储边界和全局 redaction。
- SQLite WAL v9、备份/恢复 CLI、Compose/Caddy 及运维脚本。
- 认证只读 outcomes API、近一月摘要/完整页、桌面 App 浅色视觉与右下角非模态自动提醒。
- 首次登录、刷新或重开页面从当前服务端水位开始；同页断线重连按显式游标补发遗漏事件。
- Top3 上涨百分比使用鲜明红并响应式放大；下跌保持绿色。

## 部署资产边界

- Web branch 的实际部署源码位于其独立 worktree 的 `deploy/`。
- main 不复制 Web server、worker 或部署实现；main 的 `deploy/web/README.md` 仅是边界索引，不是第二套资产。
- 当前 `stock.hazelkahlil.com` 由 Mac Docker + Cloudflare Tunnel 提供；本次仅替换精确
  Web/Worker 镜像，没有修改域名、Tunnel、账号或部署配置。
- 真实 `.env`、Token 和 master key 不进入 main、基准文档、日志或打包制品。

## 下一步

1. 在下一真实交易日验收 09:45/14:45 右下角提醒、次日同点结算/有界重试、
   强异动与全日持续运行。
2. 浏览器完全关闭后的 Web Push 如仍需要，作为非阻断的独立后续功能；当前页面内
   提醒与浏览器 Notification 不因此降级。
3. 现场门通过后，由 Human Owner 决定是否建立受控集成提交；在此之前保持
   branch/worktree/Bundle 完整并继续 `BLOCKED / NOT_ACCEPTED`。
