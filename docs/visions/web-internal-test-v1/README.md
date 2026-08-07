# web-internal-test-v1 — Web 内部试用版

- 锚定提交：`502a447d7e593d638ea45518f2a5e4d4827f683f`（唯一业务基线，tag `mac-v1-reliability-rc3-20260806`）
- 工作分支：`web/internal-test-v1-handoff`
- 合同来源：交接包 `StockWatcher-Web-Internal-Test-Handoff-20260807`（00-17 文档 + contracts + database + deploy + tests + fixtures）
- 拓扑冻结：`Browser -> Caddy -> FastAPI Web(1 进程) -> SQLite WAL <- 唯一 Worker`
- 目标：2–5 名内部测试者、单域名、单实例、无自动交易
- 验收门：基线回归、fixture parity、服务/并发/安全测试、Docker 双 worker、备份恢复回滚、VPS preflight（pending）、完整交易日 18 条（pending）
- 首答清单：`01-first-response-checklist.md`（18 项基线与架构确认）

## 完成状态（2026-08-07）

- 实现完成：依赖拆分、无 Qt 服务、schema v7、REST/WS、auth/CSRF/RBAC、唯一 Worker、
  CLI、Jinja2 UI、Docker/Caddy、运维脚本、exporter Top20 修复。
- 测试：391 passed / 20 skipped / 2 deselected（Python 3.12.11 真实重跑）；
  Ruff + Mypy strict 全绿；no-Qt import gate；fixture parity（Top20==reconstructed）；
  浏览器 E2E 13/13；容器双 worker 安全退出；备份/恢复演练通过。
- 关键修复：SQLite WAL 双进程并发损坏（-shm/-wal 生命周期竞争）→ 每线程常驻连接，
  容器内 90s 并发压测验证（证据 evidence/concurrency/）。
- Live：VPS preflight 与完整交易日 18 条验收 pending（无 VPS/Token，不伪造）；
  通过等级 A。
- 最终交付包：`../StockWatcher-Web-Internal-Test-Final-Handoff-20260807/`
