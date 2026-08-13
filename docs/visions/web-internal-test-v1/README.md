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
  初始化延迟而错过租约续期。已过期租约不能由原 Worker 复活，运行时写入发现 fencing
  失效会立即停止业务线程并交给容器重启。
- Web 对 Worker 写入的 Top3、命令状态、事件和健康证据使用独立只读短连接，避免容器重启
  后长连接停留在旧 WAL 视图，出现“Worker 已有 3 只、网页仍为空或命令一直等待”。
- 代码验证：`433 passed / 25 skipped / 2 deselected`，Ruff、Mypy、原生 JS 语法、workspace
  validator 与 `git diff --check` 全绿。新部署与交易日真实 Top3 验收仍待完成，状态继续保持
  `BLOCKED / NOT_ACCEPTED`。

## 2026-08-12 次日复盘与提醒视觉返修

- 从 Shared Core 接入严格 `/trade_cal` 质量契约和最多五次的持久化分钟重试；历史 30 天
  回补不绕过上限，旁路线程不参与候选、StableTop3、固定提醒或强异动决策。
- Web Schema v8→v9 纯新增 `candidate_outcomes`，迁移保留账号、会话、命令和事件；新增认证
  只读 outcomes API、首页近一月摘要与近 5/20 个入选交易日/全部的完整复盘页。
- Web 使用莫兰迪典藏编辑部配色；强异动及固定 09:45/14:45 自动提醒改为中央手动关闭
  `alertdialog`，保留原生通知、提醒中心及 WebSocket 新事件水位去重。
- 当前仍由 Mac Docker + Cloudflare Tunnel 承载；离线门与部署成功均不能替代完整交易日
  现场验收，状态继续 `BLOCKED / NOT_ACCEPTED`。
- 部署前 `bf447ba` Schema v8 备份已完成 checksum 与 integrity 校验；`d2dfc90` 单次迁移到
  Schema v9 后，账号、会话、命令、事件数量保持，Web/Worker/Tunnel 与公网健康通过。公网
  登录后的手动抓取和提醒仍留给 Human Owner 现场复核，不以匿名健康检查代替。

## 2026-08-12 Top3 涨幅强调与零告警收口

- Top3 候选卡内的上涨百分比单独恢复旧版鲜明红，字号响应式放大为
  `1.85rem–2rem`；下跌保持莫兰迪绿，其他上涨语义保持低饱和红，布局未改。
- 完整离线回归在 `-W error` 下为 `499 passed / 25 skipped / 2 deselected`、零告警；
  Ruff、Mypy、workspace validator、Windows package contract、JavaScript 语法全绿，生产与全锁定
  依赖审计均为无已知漏洞。
- 部署实现提交为 `cfc6cd66be3edcf5468840c29b509eae643896b5`，镜像为
  `stockwatcher-web:web-alpha4-cfc6cd6`。部署前后备份的 checksum、`integrity_check=ok`、Schema v9、
  Web/Worker/Tunnel 健康与公网 `app.css?v=13` 均已校验。
- 当前结构化 Review 为 `P0=0 / P1=0 / P2=0`；本轮 Web 仅剩真实交易日固定提醒、
  同点结算/重试与全日运行现场验收，在此之前继续 `BLOCKED / NOT_ACCEPTED`。

## 2026-08-13 CNB Web 时间窗试运行

- Human Owner 授权只迁移独立 Web 运行线到 CNB；不迁移 macOS/Windows 桌面端，不改现有
  Mac Docker、域名或 Cloudflare Tunnel，也不把 Tushare SDK/API 扩展成任意网页抓取。
- CNB 私有目标仓库为 `kahlils-stockwatcher/stockwatcher-web`。CNB `main` 仅作为本地独立
  `feat/web-cnb-devspace` 配置线的运行镜像，不等同于 StockWatcher 本地日常开发 `main`。
- `.cnb.yml` 在工作日上海时间 `08:25` 启动 2 核 only-preview 工作空间，运行 Web 与唯一
  Worker 到 `16:15`；8 小时离线保活低于 CNB 18 小时上限，没有伪造心跳或绕过回收。
- `.cnb-runtime/` 被 Git 忽略；主密钥只在私有运行目录中使用，并通过独立私有制品跨空间恢复。
  SQLite、报告和校验和通过私有 Docker 制品标签 `cnb-results-latest` 与当日标签归档，不把
  主密钥与结果快照放在同一制品中。
- 结果快照使用项目已有 SQLite Online Backup API，并在恢复前校验外层 SHA-256 与备份内
  `SHA256SUMS.txt`；Token 仍由 Owner 在 HTTPS 管理页录入，管理员密码只在私有终端交互输入。
- CNB 官方 `cnb-pipeline` Skill 校验 `.cnb.yml` 和 `.cnb/web_trigger.yml` 的 YAML、语义、
  目录与 Schema 均通过；CNB 专用镜像本地构建通过，镜像内 Web 导入、Docker CLI、非 root
  UID/GID 及无全局 pip 检查通过；临时 Schema v9 下 Web、Worker、live/ready 健康通过。
- `09:00–16:00` 是配置目标，不宣称平台 SLA。CNB 故障、额度耗尽、供应商异常或 Token 未
  完成录入时继续 fail-closed；次日完整交易日现场验收前保持 `BLOCKED / NOT_ACCEPTED`。
- 首次人工登录暴露出终端初始化会原样保存密码首尾不可见空白、而 Web 登录框也会按原值认证；
  CNB 初始化脚本现拒绝首尾空格/制表符，并提供只从标准输入读取的 `reset` 模式。重置会撤销
  该账号既有会话，密码与哈希均不回显。修复后的实际登录与 Token 录入仍需 Owner 现场完成。
- CNB Runner 现场日志进一步确认 `backup: true` 的工作区备份只处理 Git 可见改动；被安全忽略的
  `.cnb-runtime/` 会被报告为“没有需要备份的内容”，因此不能承担跨工作空间持久化。运行线改为
  显式恢复两个仓库私有 Docker 制品：`cnb-secrets-v1` 只含主密钥与校验和，
  `cnb-results-latest` 只含数据库、报告与校验和。两者都不进入 Git，缺少或校验失败时预览
  fail-closed；管理员首次创建后必须上传两个制品，实际跨空间登录仍需重新现场验收。
- 现场主动停止 `cnb-rdg-1jvrgo8lo` 时，流水线虽为 success，但退出钩子未执行，制品最后推送
  时间仍停在停止前；因此不再把终止信号当作唯一持久化保证。手动预览改为运行中每 60 秒、
  工作日预览每 15 分钟上传一次已校验快照，原 11:35/15:55/16:10 与正常退出快照继续保留。
- 2026-08-13 08:25 的首次定时构建 `cnb-nso-1jvs84381` 在 Prepare 阶段被 CNB 拒绝：
  `crontab` 事件不能直接使用 `vscode` 服务。定时任务已改为 `cnb:apply` 触发同仓库的
  `api_trigger_cnb_preview` 子流水线；只有该 API 子流水线挂载 `vscode`，避免再次触发平台
  事件限制。随后手动 API 构建 `cnb-pco-1jvs8bn88` 已通过 Prepare、创建运行中的
  only-preview 开发空间并到达 StockWatcher 登录页，事件路径现场验证通过；管理员登录和
  Token 激活仍待 Owner 在新空间完成。

## 2026-08-13 重启恢复与候选胜率修复

- SQLite 写连接改为 `synchronous=FULL`；Web 与 Worker 的正常退出会等待 outcome sidecar
  写入、执行 WAL checkpoint 并关闭持久连接，Compose Web/Worker 均保留 120 秒停止宽限期；
  Worker 租约心跳对短暂持久化写锁最多等待 5 秒，仍小于默认 20 秒 TTL。
- 启动时不再只看 SQLite 文件头：完整性检查发现 `database disk image is malformed` 等损坏时，
  会在跨进程恢复锁内从已验证的迁移备份或 `/backups` 备份恢复，先隔离主库及其 WAL/SHM，
  再原子替换并重新校验；恢复前会关闭当前线程旧连接，避免继续读取已隔离的损坏 inode；
  没有可靠备份则保持只读失败关闭。CNB 已增加“已有数据库校验失败→上一次私有结果快照恢复”的
  启动兜底。
- Web 的 09:45 与 14:45 提醒会在同一 SQLite 事务中把各自 3 只候选写成 pending outcome，
  日历解析和结算仍在旁路线程执行；重启不会丢失当天 6 条胜率样本。下次启动会重新解析尚未
  完成的目标交易日。
- 胜负统一按入选价到目标时点价的真实 `return_pct` 判定：例如从 +10% 回落到 +8% 仍是正收益，
  计为赢；历史记录若保留了错误标签，复盘读取时也会按真实收益纠正。返修提交为
  `a69b823`、`5597495`、`0b623d0`；离线回归为完整 `pytest 508 passed / 26 skipped`、
  目标集 `145 passed`、Ruff、Mypy、workspace validator 与 shell 语法检查通过。以上证据未替代
  下一真实交易日的 09:45/14:45 同点行情验收，状态继续 `BLOCKED / NOT_ACCEPTED`。

## 2026-08-13 Web 桌面视觉与提醒生命周期返修

- Web 首页按 Human Owner 提供的桌面 App 截图重构为浅色状态总览、三行横向候选卡、底部操作区
  和只读声明；提醒改为右下角非模态窗口，不加遮罩、不锁滚动、不抢焦点，按钮与 Esc 均可关闭。
- 一次关闭会清空当前提醒及页面内已经排队的提醒展示，避免旧历史连续顶替成“关不掉”；事件记录
  仍保留在提醒中心，关闭以后新到达的真实事件仍可再次显示。
- WebSocket 首次连接不再发送 `after_id=0`：服务端以当前事件水位建立新页面会话，登录、刷新、
  关闭页面后重开均不会把 30 天持久化历史当作新弹窗；同一页面断线重连仍发送显式游标并补发
  断线期间遗漏事件。Dashboard 与提醒中心都在建连前注册监听器，消除首个 hello/事件竞态。
- 定向契约回归 `35 passed`；本机 Chrome 临时测试账号已验证实时连接、桌面布局、右下角定位、
  按钮/Esc 关闭、队列清空、后续新提醒、刷新和新开页面不重放。测试使用临时 SQLite 与合成候选，
  测试站点和临时数据已销毁；本轮未部署，不能替代真实交易日现场验收，状态继续
  `BLOCKED / NOT_ACCEPTED`。
- 完整 `-W error` 门首次暴露两个慢回调测试只等待“租约已创建”、未等待慢回调真正开始的时序
  竞态；测试现用临时 marker 同步到实际慢 tick/runtime heartbeat 后再量测独立 lease heartbeat，
  不放宽 12 秒阻塞窗口，也未改 Worker 产品逻辑。最终完整回归为 `509 passed, 26 skipped`、
  零告警。
- 实现提交 `53501ad` 已快进合入本地 `cnb/main`；未 push、未构建或替换线上镜像。

## 2026-08-13 Murphy Review 与本地合并收口

- 复核断电、数据库恢复、旧浏览器标签页和网络慢响应组合后，发现并修复两项可复现边界：浏览器
  游标高于恢复后数据库的新水位时会长期收不到事件；提醒事件等待 REST 状态时，用户关闭动作可能
  先完成，慢请求返回后又把旧提醒入队，表现为“关不掉”。
- WebSocket 现把高于服务端水位、低于保留窗口、负数或非法格式游标统一转为显式
  `server.resync_required`；浏览器收到重同步后重置水位、关闭旧弹窗并清理已处理提醒 ID，避免
  恢复后的 ID 复用继续误去重。
- 提醒事件改为按到达顺序同步入队，候选状态只使用最近缓存并异步刷新；关闭动作记录已接收边界并
  清空当前队列，因此慢 REST 不再能把关闭前的提醒重新弹出。异步监听器 rejection 也被逐监听器
  隔离，不影响其他页面事件。
- 返修提交 `214563c` 已直接进入本地 `cnb/main`。Murphy 定向回归 `32 passed`；完整门为
  `512 passed, 26 skipped`、`-W error` 零告警，Ruff、Mypy（142 source files）、全部 JavaScript
  语法、workspace validator、lock、Windows 离线打包合同与 `git diff --check` 全绿。
- 最终结构化 Review 为 `P0=0 / P1=0 / P2=0`。未 push、未部署、未替换线上镜像；数据库恢复与
  弹窗竞态的代码证据不能替代下一真实交易日现场验收，状态继续 `BLOCKED / NOT_ACCEPTED`。

## 2026-08-13 Web UI 上线与恢复备份返修

- 桌面浅色 UI、右下角提醒与 Murphy 返修先以源码 `52cd13a` 构建并替换线上 Web/Worker；公网
  `app.css?v=14`、`app.js?v=6` 与 `dashboard.js` 均和该源码字节一致。
- 发布后备份检查现场发现主库 `idx_service_leases_expiry` 索引损坏；Worker fail-closed 退出，
  readiness 返回 503，自动恢复又因只匹配 live DB 文件名和单层目录而选中较旧迁移备份。
  未把这次自动恢复当作成功：立即停止 Web/Worker，使用已校验的发布后完整备份恢复 Schema v9。
  恢复后关键计数为 `scan_attempts=1696`、`candidate_outcomes=18`、`web_users=6`、
  `web_events=12331`、`alert_events=18`，与发布前/发布后快照一致。
- `33bc3ea` 让自动恢复递归发现 `/backups` 下运维分组目录中的 `stockwatcher.sqlite3`，并新增
  “最新嵌套运维备份优先于旧迁移备份”回归；`34ce825` 让在线备份通过只读源连接执行 SQLite
  Backup API，避免备份容器成为第三个 WAL writer。
- 最终运行镜像为 `stockwatcher-web:web-alpha4-34ce825`，运行源码
  `34ce825014692aef01ae397499dd7604c67273ef`。恢复后在线备份
  `/backups/stockwatcher-postfix-20260813T111830Z/stockwatcher-20260813T191830Z` 的 checksum、
  `integrity_check=ok`、外键 0、Schema v9 与关键计数均通过；live DB 同步保持完整。
- 完整离线门为 `513 passed, 25 skipped, 2 deselected`、`-W error` 零告警；Ruff、Mypy
  （142 source files）、JavaScript syntax、workspace validator、shell syntax 与
  `git diff --check` 全绿。部署与恢复证据仍不能替代下一真实交易日验收，状态继续
  `BLOCKED / NOT_ACCEPTED`。
