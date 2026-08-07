# Web 内部测试轨道

## 唯一事实源

- 分支：`web/internal-test-v1`。
- HEAD：`87a8b85609f57504861e09f416694582556b736e`。
- Web 基线：`502a447d7e593d638ea45518f2a5e4d4827f683f`（Mac RC3 tag 基线）。
- Web 与 main 的共同基线是 `502a447...`；Web 有独有实现提交，不能把它当作 main 的祖先。
- 当前 worktree：`~/Documents/700-AI-Workspace/90-Archive/StockWatcher/00-current/web/source-worktree-87a8b85609f57504861e09f416694582556b736e`。
- 当前 Bundle：`90-Archive/StockWatcher/00-current/web/repository.bundle`。
- 当前 Handoff：`90-Archive/StockWatcher/00-current/web/latest-handoff.zip`。

## 状态

**`BLOCKED / NOT_ACCEPTED`。不得称为最终可部署、已上线或已合入 main。**

原因：

1. 当前 macOS worktree 的完整 pytest 现场是 `391 passed, 20 skipped, 2 deselected`，Ruff 与 Mypy 通过；但 `python3 scripts/validate_workspace.py` 被 `.venv/lib/python3.12/site-packages/playwright/.../SKILL.md` 的坏本地 Markdown 链阻断。
2. Compose config 在非敏感临时审计 env 下通过；容器、镜像 digest、SBOM 和浏览器 E2E 证据是在 macOS Docker Desktop 取得。
3. VPS preflight、真实域名/TLS、真实 Token、Linux 原生文件系统和完整交易日 18 项验收仍 pending。
4. 用户给定的 Web Audited Fix 失败边界继续有效；本轮不为“当前 pytest 通过”改写 Web 轨道为 accepted。

## 已实现范围

- FastAPI/Jinja2/原生 CSS/ES modules/WebSocket。
- 唯一 headless Worker；lease 防重复 Worker；Web 不复制 CandidateEngine、StableTop3 或自动调度。
- REST/API、Admin/Tester、Argon2id session、CSRF、RBAC、AES-256-GCM Token 存储边界和全局 redaction。
- SQLite WAL v7、备份/恢复 CLI、Compose/Caddy 及运维脚本。

## 部署资产边界

- Web branch 的实际部署源码位于其独立 worktree 的 `deploy/`。
- main 不复制 Web server、worker 或部署实现；main 的 `deploy/web/README.md` 仅是边界索引，不是第二套资产。
- 未执行域名、服务器或 GitHub 操作；不生成真实 `.env`，不保存 Token/master key。

## 下一步

1. 在隔离环境修复/重跑 workspace validator，并保存完整工程门结果。
2. Owner 提供 VPS、域名和授权后执行 `docker compose config/build`、分层数据源 preflight、备份恢复和完整交易日验收。
3. 全部门通过后，由 Human Owner 决定是否建立受控集成提交；在此之前保持 branch/worktree/Bundle 完整。
