# web-internal-test-v1 — Web 内部试用版

- 锚定提交：`502a447d7e593d638ea45518f2a5e4d4827f683f`（唯一业务基线，tag `mac-v1-reliability-rc3-20260806`）
- 工作分支：`web/internal-test-v1`
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

## 2026-08-08 本地部署前硬化

- 托管路线复核：OpenAI Sites 使用 Cloudflare Worker/D1，无法原样运行本项目的 Python
  FastAPI、Tushare SDK、常驻唯一 Worker 与 SQLite WAL 双进程拓扑；未创建只具备静态页面、
  却不能真实扫描的 Sites 项目。保持冻结的专用 VPS Docker + Caddy 路线。
- 安全修复：生产登录 Cookie 强制 `Secure + HttpOnly + SameSite=Lax`；生产环境关闭公开
  OpenAPI；阻止停用或降权最后一个启用管理员；严格校验用户启用状态布尔值；补齐应用层
  与 Caddy 安全响应头；移除会被 CSP 拦截的内联样式。
- 镜像减面：一次性 `pip-audit` 只命中基础镜像自带 `pip 25.0.1`；生产运行阶段已移除全局
  pip，重建后确认 `pip_present=false`，Web/Worker 模块仍可导入。Docker Scout 因本机未登录
  Docker ID 未执行，基础系统镜像完整 CVE 扫描仍为部署前待办。
- 本地验证：Web 回归 `38 passed / 5 skipped`；Ruff 全绿；Mypy `133 source files` 全绿；
  全部原生 JS 语法、workspace validator 与 `git diff --check` 通过；Compose config 与 Caddy
  validate 通过。
- 双容器烟测：临时主密钥 + 临时 SQLite 下，Web ready、schema v7、唯一 Worker lease 与
  心跳均正常；生产 Cookie 三项安全属性均为 true，应用安全头生效，生产 OpenAPI 返回 404。
  临时容器、数据库和主密钥已销毁，仅保留本地预发布镜像
  `sha256:040451e09ea42d3a3923ebe28ab48d67eb7eac27a78dad26d696178dae85dfcb`
  （UID/GID `10001:10001`，dirty/preflight，不是发布镜像）。
- 已生成本地不可变源码提交，并验证可用完整 40 位提交标签快速重建 UID/GID
  `10001:10001` 的部署候选镜像；候选镜像未上传到任何远端仓库，最终摘要在部署交接中记录。
- 仍未完成：VPS/DNS/TLS、首个管理员和 2–5 个测试者、
  HTTPS 管理页加密录入 Token、VPS 出口 IP 数据源 preflight、完整交易日 18 条验收。
  当前交付状态继续保持 `BLOCKED / NOT_ACCEPTED`。

## 2026-08-08 最新界面批注与托管确认

- 标题改为上海时区实时日期、星期与 `实时Top3`，在 1146px 宽视口按批注呈现 51.6px，
  手机断点回落至 36px；页面底部说明栏已删除，静态资源缓存版本同步递增。
- Human Owner 明确要求网页端必须同时具备 FastAPI、Tushare SDK、常驻唯一 Worker 与
  SQLite WAL 的真实扫描能力；OpenAI Sites 不能承载该 Python 双进程拓扑，正式路线确认使用
  专用 VPS Docker + Caddy，并由 Cloudflare 将 `stock.hazelkahlil.com` 指向唯一公网入口。
- 本地浏览器已核对动态星期、标题计算字号与页脚不存在；Web 定向回归、Ruff、Mypy、原生 JS
  语法、workspace validator 与 `git diff --check` 通过。VPS 主机访问、DNS/TLS、秘密注入与
  VPS 数据源 preflight 仍未产生现场证据，状态保持 `BLOCKED / NOT_ACCEPTED`。
