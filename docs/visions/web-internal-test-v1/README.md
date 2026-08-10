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
- 部署安全复核补齐 Docker 构建上下文排除：Git 元数据、虚拟环境、`.env`、Docker secret、
  SQLite、日志、报告、备份与运行证据均不会发送到构建器；镜像复制白名单仍只包含依赖清单、
  README 与 `src/`。
- 本地浏览器已核对动态星期、标题计算字号与页脚不存在；Web 定向回归、Ruff、Mypy、原生 JS
  语法、workspace validator 与 `git diff --check` 通过。VPS 主机访问、DNS/TLS、秘密注入与
  VPS 数据源 preflight 仍未产生现场证据，状态保持 `BLOCKED / NOT_ACCEPTED`。

## 2026-08-08 Worker lease hardening and local tunnel redeploy

- Web 分支新增提交 `b1ba8e36c69c959bdf95f34e218c0ba85b25ec6d`：Worker lease fencing 改用
  独立短连接，不再与业务 SQLite 连接共享心跳路径；lease 丢失时快速退出，由 Compose
  重启，避免 SQLite 异常等待把 Worker 留在 `unhealthy`。
- 本地 Cloudflare Tunnel 已加载镜像 `stockwatcher-web:web-internal-test-v1-b1ba8e3`；
  Web、Worker、Caddy、origin、HTTPS edge 和 Worker lease 健康检查均通过，已跨过此前约
  4 分钟的 lease 过期窗口。运行容器 UID/GID 仍为 `10001:10001`。
- 当前仍无活动 Tushare Token；Worker preflight 安全退出并报告 `no active token`，因此
  尚未产生真实候选。管理员仍需通过 HTTPS Admin 页面输入 Token，随后再做 1/100/300/800/full
  preflight 与完整交易日 18 轮验收；状态继续保持 `BLOCKED / NOT_ACCEPTED`。

## 2026-08-09 全仓 Review 返修（本机基准）

- Human Owner 暂缓 VPS、Windows 和中国大陆网络验证；先完成本机 Mac 代码返修，随后明确授权
  部署到现有 Mac Docker；不改账号/DNS，也不读取或回显任何凭据。
- 行情与候选 fail-closed：生产证券池至少 4500 只、行业覆盖至少 95%；日线逐行匹配请求
  交易日；缺最新日线、复权因子变化和当日复牌进入机械跳变排除；跨交易日清空盘中滚动
  基线，混合日期和日期回退停止候选；强制刷新重新读取证券列表与日线。
- Worker/SQLite：租约获取使用 `BEGIN IMMEDIATE`；Worker 业务事务在同一写事务内校验
  holder、fencing token 和 expiry；过期租约使 readiness 返回 503；命令完成绑定 attempt，
  Worker 忙时不提前 claim，关停时取消并等待命令线程，未退出则不主动释放租约。
- WebSocket/命令：hello 不再推进浏览器游标；过期游标可 resync；隐藏事件通过安全 cursor
  前进；事件按角色和命令 requester 过滤；慢客户端有发送超时。schema v8 仅放宽
  `command.updated` 的 source dedupe，保留每次状态迁移。
- 账号与秘密：同一 session 使用稳定 CSRF，命令限流真实消费额度；密码修改撤销该用户全部
  session；最后一个启用管理员在原子事务内保护；Token 第三次轮换先替换旧 previous；CLI
  删除 argv 密码参数；命令详情仅 requester/admin 可见，共享手动刷新只暴露最小状态。
- 运维：restore 完整替换 reports 目录，避免遗留旧 PDF；provider preflight 修复 `full`
  解析，并校验证券数量/唯一性/行业覆盖、实时覆盖、唯一代码、真实 `source_ts`、交易日、
  交易时段、新鲜度和扫描跨度。
- 本机部署首次 schema v8 迁移返回 `database disk image is malformed`，服务保持停止并保留
  失败现场；随后从升级前备份恢复，验证 `integrity_check=ok`、schema v7、外键问题 0 后，
  重新迁移到 schema v8 成功。
- 恢复演练暴露 reports 命名卷挂载点不能整体重命名；提交 `ec00089` 改为在挂载卷内 staging
  和 rollback，定向测试、Ruff、Mypy 通过，真实容器恢复返回 `reports_restored=true`。
- `ec00089` 启动后数分钟再次出现 `database disk image is malformed` 和 Worker lease
  重启；服务立即停止并保存主库、WAL/SHM 与日志现场，没有把 unhealthy 状态当成部署成功。
- 提交 `7ea43cc` 将数据库 restore 改为校验备份、同卷 staging、关闭当前线程连接、隔离旧
  主库及 WAL/SHM、原子替换并再次校验；迁移备份也不再复用旧 sidecar。SQLite/Worker/Web/
  readiness 扩大回归 42 项通过，Ruff、Mypy 与 `git diff --check` 通过。
- 本机代码验证：`413 passed / 25 skipped / 11 deselected`；11 项中 9 项为当前受限沙箱明确
  排除（6 个 QLocalServer socket、3 个已安装 App 真实数据库合同），另 2 项为 live_tushare；
  Ruff、Mypy（136 source files）、原生 JS 语法、workspace 29 项和 `git diff --check` 通过。
- 本机 Docker 当前运行镜像 `stockwatcher-web:web-internal-test-v1-7ea43cc`；Web、Worker、
  tunnel gateway、cloudflared、origin、公开 HTTPS live/ready 均通过，运行源码为 `7ea43cc`。
  恢复来源为已验证的 schema v8 备份，启动后已跨过此前复发窗口且未再出现 malformed/lease
  丢失日志。
- 当前仍为 `BLOCKED / NOT_ACCEPTED`：静态/回归验证不能替代下一交易日的真实全市场、Top3、
  09:45/14:45、强异动和 15:30 现场验收。

## 2026-08-09 Web favicon

- 使用 Human Owner 提供的六边形 StockWatcher 图，机械去除白色背景并裁成真实 Alpha 透明
  1024×1024 主图；导出 16/32/48 多尺寸 ICO、32×32 PNG 和 180×180 Apple Touch Icon。
- `base.html` 为所有页面声明 favicon 与 Apple 图标，资源 URL 使用 `v=1` 做首次缓存隔离。
- `tests/test_web_auth_api.py` 新增模板链接与 PNG/ICO 魔数检查；完整 Web 认证/模板回归
  18 项、Ruff、图像尺寸和 Alpha 检查均通过。
- 本机 Docker 当前运行 `stockwatcher-web:web-internal-test-v1-7528f38`；公网 HTML 已回读
  三条图标链接，公网 32px PNG 与仓库文件 SHA-256 完全一致，健康检查保持通过。
- 此项只完成网页图标，不改变候选规则、账号、Token、DNS 或交易日验收边界；整体状态继续
  保持 `BLOCKED / NOT_ACCEPTED`。

## 2026-08-10 Worker 调度与健康边界修复

- 修复手动刷新命令被启动阶段自动扫描长期阻塞的问题：Worker 先领取排队命令，自动扫描改为
  受管线程；自动扫描忙时命令保持 `queued`，到安全边界后再领取，不会错误显示为已运行。
- 新增 Worker 主循环、扫描开始/结束和 watchdog 运行证据；健康检查不再只看独立租约心跳，
  卡在供应商准备或扫描超过安全时限时会返回不健康并由 Docker 重启 Worker。watchdog 失败时
  明确记录“本次未产生新候选”，不包装旧 Top3。
- 网页刷新进度显示 queued/running/失败状态，失败会明确说明本次没有新候选；前端等待时间与
  后端命令生命周期对齐，避免 75 秒后静默放弃轮询。
- 首次 Web 启动没有静态基础缓存时，Worker 会按退避策略建立一次证券池/日线/板块缓存，
  不再每 10 秒重复强制刷新；供应商限流、Token 或缓存失败会保留为可解释的失败状态。
- Worker 在取得唯一租约后立即启动独立心跳，再写运行时启动证据；完整测试负载下不会因
  初始化延迟而错过租约续期。
- 代码验证：`429 passed / 25 skipped / 2 deselected`，Ruff、Mypy、原生 JS 语法、workspace
  validator 与 `git diff --check` 全绿。新部署与交易日真实 Top3 验收仍待完成，状态继续保持
  `BLOCKED / NOT_ACCEPTED`。
