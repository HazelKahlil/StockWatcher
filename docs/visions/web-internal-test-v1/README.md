# web-internal-test-v1 — Web 内部试用版

- 锚定提交：`502a447d7e593d638ea45518f2a5e4d4827f683f`（唯一业务基线，tag `mac-v1-reliability-rc3-20260806`）
- 工作分支：`web/internal-test-v1-handoff`
- 合同来源：交接包 `StockWatcher-Web-Internal-Test-Handoff-20260807`（00-17 文档 + contracts + database + deploy + tests + fixtures）
- 拓扑冻结：`Browser -> Caddy -> FastAPI Web(1 进程) -> SQLite WAL <- 唯一 Worker`
- 目标：2–5 名内部测试者、单域名、单实例、无自动交易
- 验收门：基线回归、fixture parity、服务/并发/安全测试、Docker 双 worker、备份恢复回滚、VPS preflight（pending）、完整交易日 18 条（pending）
- 首答清单：`01-first-response-checklist.md`（18 项基线与架构确认）
