# Changelog

## [0.6.0-alpha.4] - 2026-08-12

### Fixed

- 交易日历仅接受 `tushare_15000 + /trade_cal + 四字段 + DEGRADED/MISSING`，明确拒绝
  HEALTHY、STALE、字段漂移、空响应、日期越界和矛盾状态。
- 收盘后网络、限流和服务器异常保持 pending，并按 SQLite 中的有界时间恢复；实时与历史
  回补统一最多五次，历史 30 天回补不再绕过尝试上限。

### Web

- Web Schema v8→v9 增加独立 `candidate_outcomes` 及重试字段；迁移前备份、事务回滚、
  `integrity_check` 与账号、会话、命令、事件保留均有回归测试。
- 新增认证只读 `GET /api/v1/outcomes?range=week|month|all`、首页近一月摘要和完整次日复盘页；
  普通响应只展示中文原因，不暴露内部 `safe_reason`。
- 首页改为与桌面 App 一致的浅色状态总览、三行候选卡和底部操作区；强异动及
  09:45/14:45 自动提醒改为右下角非模态 `alertdialog`，不加遮罩、不锁页面、不抢焦点，
  支持按钮和 Esc 关闭。
- 新页面从服务端当前事件水位开始，刷新或重开不再重放持久化旧提醒；同页断线仍按显式游标
  补发。数据库恢复造成事件 ID 回退、无效游标或过期游标时强制重同步，并清理页面旧提醒状态。
- 弹窗事件不再等待 REST 请求后才入队；关闭会同步清空当前队列并建立已处理边界，避免慢请求
  完成后把刚关闭的旧提醒再次弹出。
- Top3 候选卡的上涨百分比单独恢复旧版鲜明红，并放大为响应式
  `1.85rem–2rem`；下跌仍用莫兰迪绿，其他页面和状态仍保持低饱和色系。
- 消除 Starlette 已弃用 HTTP 测试客户端及测试资源未关闭告警，完整回归在
  `-W error` 下零告警通过；同时升级安全依赖并对生产与全锁定依赖执行漏洞审计。

### Evidence boundary

- 离线代码门、Mac Web 部署和安装包不能替代真实交易日 09:45/14:45 验收；Web 当前
  Murphy Review 返修后为 `P0=0 / P1=0 / P2=0`，但在现场验收前继续
  `BLOCKED / NOT_ACCEPTED`。

## [web-internal-test-v1-favicon] - 2026-08-09

### Added

- 将 Owner 提供的 StockWatcher 六边形图标处理为真实 Alpha 透明 favicon，提供 16/32/48
  多尺寸 ICO、32px PNG、180px Apple Touch Icon 和 1024px 主图。
- 所有页面模板声明 favicon 与 Apple 图标，并通过版本参数避免浏览器继续使用旧缓存。

### Deployment

- 本机 Docker 已加载 `7528f38`；公开页面图标链接、静态文件字节哈希以及
  Web/Worker/Tunnel readiness 均验证通过。

## [web-internal-test-v1-review-hardening] - 2026-08-09

### Fixed

- 加固全市场完整性、日线交易日、机械跳变、跨日滚动基线和旧数据恢复门。
- 为 Worker 业务写入增加 lease/holder/fencing/expiry 同事务保护，并修复命令 attempt、忙碌
  claim、关停等待、WebSocket 重放/权限/慢客户端和过期 readiness。
- schema 升至 v8，保留每次 `command.updated` 状态迁移；修复 Token 三次轮换、命令限流、
  多标签页 CSRF、密码改动会话撤销、最后管理员竞态和命令详情越权。
- restore 改为完整替换 reports；provider preflight 严格验证全市场覆盖和真实源时间。
- 真实 Docker 恢复演练补齐 reports 命名卷挂载点路径：在卷内 staging/rollback，避免重命名
  挂载点导致恢复失败，同时确保旧报告不会残留。
- SQLite restore 改为同卷 staging 后原子替换主库，并把旧主库及 WAL/SHM 隔离为
  `.restore-old`；迁移备份也先清理旧 sidecar，避免恢复后重放不属于新快照的页面。

### Status

- 本机 Docker 已加载 `7ea43cc`；schema v8、SQLite 完整性、外键、Web/Worker/Tunnel、公开
  readiness 和部署后备份均已验证。交易日现场验收待执行，状态保持 `BLOCKED / NOT_ACCEPTED`。

## [web-internal-test-v1] - 2026-08-07

### Added
- Web 内部试用版：FastAPI 单进程 + 唯一 Worker + SQLite v7（依赖拆分 core/desktop/server/dev）。
- 无 Qt `StockWatcherService` 编排（扫描/自动化/提醒/总结/命令），与 Mac 桌面共用 engine/runtime/storage。
- schema v7：users/sessions/user_state/lease/commands/secret_requests/encrypted_secrets/events/public_state/audit。
- 认证：Argon2id、opaque session、CSRF、RBAC、登录/命令限速；AES-256-GCM Token（先测后激活）。
- REST API（contracts/openapi.yaml）、WebSocket 事件泵（after_id/resync/backpressure）。
- 运维 CLI：migrate/create-user/backup/restore/provider-preflight/healthcheck。
- Docker Compose + Caddy（digest 锁定）、运维脚本、VPS preflight 脚本。
- 修复 legacy exporter：`raw-top20` 输出真 20 行 + 显式 `raw-top3`。

### Fixed
- SQLite WAL 双进程并发损坏：每线程常驻连接（-shm/-wal 生命周期竞争）。

### Testing
- 391 passed / 20 skipped / 2 deselected；Ruff、Mypy strict、no-Qt gate、
  fixture parity、浏览器 E2E 13/13、容器双 worker、备份/恢复演练。

### Pending
- VPS 数据源 preflight 与完整交易日 18 条 Live 验收（需 Owner 提供 VPS/域名/Token）。

本项目采用 [Semantic Versioning](https://semver.org/)；所有值得用户或维护者关注的变化记录在这里。

## [Unreleased]

### Mainline

- 2026-08-12：在 `0.6.0a3` 修正次日同点复盘的生产交易日历 wire endpoint 契约：
  `Tushare15000Provider` 继续创建逻辑 `endpoint="/"` 请求，`TushareSdkProTransport`
  改写并实际请求 `/trade_cal`，`CandidateOutcomeTracker` 只接受该精确 provenance endpoint。
  新增 fake Session + 内存测试凭据的完整离线传输链回归，错误 endpoint/profile/字段、
  非受控 DEGRADED、STALE、空响应、越界和矛盾日历继续 fail closed。
- 2026-08-11：在 `0.6.0a2` 完成桌面端“次日同点复盘”契约返修：正式 09:45/14:45
  三只候选按
  下一真实交易日同档行情做理论复盘，展示近 5/20 个入选交易日与全部记录的个股胜率、
  平均/中位收益、分档统计和完整六笔日组合胜率。功能不连接交易账户、不自动下单，且
  复盘失败不得阻塞原实时扫描、Top3、提醒或弹窗。
- 2026-08-11：建立 `v0.4.0-alpha.2` Mac / Web / Windows 内部试用源码基准；Shared Core
  应用代码为 `ad04e39`，Web 固定 `bf447ba` 并继续 `BLOCKED / NOT_ACCEPTED`，Windows
  PR #4 已达到 `WINDOWS_SMOKE_PASS`。该版本不代表权威 M0、完整三平台验收或商业稳定。
- 2026-08-01：将 Mac V1 内部试用成果及其完整 Git 历史整合到唯一主目录的本地 `main`。
  当前主线包含真实全市场扫描、行业/概念板块、稳定 Top3、候选池强异动、09:45/14:45
  调度、30 天历史、15:30 盘后总结和 Mac arm64 App；`fix/macos-v1-internal-acceptance`、
  `feat/macos-v1-port`、`fix/shared-v1-selection-completion` 与
  `feat/windows-v1-real-candidates` 仅作为历史来源，不再是当前开发主线。
- 当前状态为 Mac V1 内部试用主线，不是商业稳定发布版。新鲜 09:45/14:45、15:30 准点、
  无旧缓存冷启动、睡眠/断网恢复和 Windows 独立验收继续在 `main` 推进。

### Security

- Web 安全审计返修：WebSocket 周期重验并在注销、改密、停用、升降权后关闭；严格
  Origin/Host/可信代理边界；Argon2 线程池与并发门、账户/IP/全局限流、输入和请求体上限；
  Session touch 节流与需备份确认后启用的 Session/命令/审计保留策略；公开 readiness 最小化；发布分支触发
  Web 专用 CI 与依赖变更审查。GitHub 分支保护和部署审批仍需 Human Owner 在仓库设置中启用。
- 普通 UI 收敛为一个 Tushare Token；Pro 代理与原生实时共用系统安全存储中的 Primary
  凭据（Windows Credential Manager / macOS 系统钥匙串）。SDK 调用只临时注入内存并恢复，
  不调用 `set_token()`，不把 Token 写入源码、配置、SQLite、日志、截图、fixture、命令行或安装包。

### Changed

- SQLite 从 v7 升到 v8，为 `candidate_outcomes` 增加
  `settlement_attempts/last_attempt_at/next_retry_at`；迁移继续使用前置备份、事务回滚、
  `integrity_check` 与只读降级，并覆盖 Windows 文件句柄释放。
- Mac/Windows 重建元数据提升为 `0.6.0-alpha.3`；本轮未构建、覆盖或重装现有 App。
- V1 主链路固定为 `fastapic.stockai888.top` 的普通/历史 Pro 请求与
  `tushare.realtime_quote(src="sina")` 的原生实时快照；旧 Super、Fast 命名路线和
  TdxQuant 只留在高级诊断。
- Human Owner 2026-07-30 改为 Mac-first：共享连接门返修先在 macOS 验证，再同步给
  Windows；Windows 真实验收仍为 `FAIL`，不得由 Mac 结果覆盖。
- Mac 首次无 Token 启动显示非阻塞的简单数据接口页；网络中断会停止定时扫描，恢复后清除
  旧基线并等待连续三轮新鲜完整数据。
- 固定提醒时间从历史规格的 14:50 改为 Human Owner 最终确认的 14:45；普通排名变化
  只更新主界面，固定时点和强异动弹窗始终包含完整三只。
- SQLite 升级为 v3，候选快照及三只明细原子保存，并提供 30 天提醒历史和每日总结。
- Tushare 主界面启动后立即、随后每 60 秒自动区分基础连接与实时快照状态；
  人工按钮改为“立即检测实时数据”；经授权的原生实时路线使用本机 Fast 凭据
  执行单证券脱敏探测，空数据、超时、权限、频控和缺供应商时间均给出明确问题
  位置，候选继续 fail-closed。
- 原生 SDK 适配器不调用会写入用户目录 `tk.csv` 的 `tushare.set_token()`；
  凭据只在受全局锁保护的单次 SDK 调用内注入内存，并在调用结束后恢复 SDK 全局状态。
- Windows 构建复用锁定的 `uv` 环境，不再假设环境含 `pip` 或临时升级
  PyInstaller；Inno Setup 不在 PATH 时只读查找官方默认安装目录。
- 默认数据路线调整为跨平台 Tushare 兼容 HTTPS：超级接口作为主接口，快速接口仅在
  同口径 M0 验证后按能力路由；TdxQuant 保留为可选诊断和资金字段探索。
- Windows 正常启动不再要求通达信或本机 TQ 服务，数据接口凭据改由系统安全存储
  管理，并采用“内存测试成功、人工确认、原子替换、重新预热”的切换流程。
- 开发方式调整为 Mac 本地优先，GitHub 仅在版本节点或明确备份/交接需求时同步。
- 版本路线重排：先完成 Mac Replay 基础和本地 Alpha，再进入 Windows/通达信真实数据闸门。
- v0.3 执行路线收敛为 Windows + 官方 TdxQuant 单人只读测试；Mac 仅保留 Mock/Replay 与离线契约验证，不购买或接入 Tushare/iFinD。

### Added

- 固定候选复盘旁路：优先复用下一交易日全市场扫描，对缺失项最多三只单次批量实时补查，
  再以 `stk_mins` 精确 09:45/14:45 一分钟 close 串行回补；零价、错日、过期、停牌、
  无成交或质量不足均标为 pending/unavailable，不计入胜率分母。日历与补查在独立单线程
  旁路运行，不延迟固定提醒；补位/非正式候选不会进入新记录或历史回补。
- 分钟回补按目标日期和 09:45/14:45 档位隔离，使用 +1/+3/+8/+20/15:05 最终确认的
  有界退避；网络、限流、服务器错误和首次空数据保持 pending，App 重启从 SQLite 恢复 due
  任务，旧积压不会抢占当前三笔。
- 历史窗口新增“提醒记录 / 次日复盘”标签和近 1 周、近 1 月、全部切换；数据库查询继续在
  Qt 工作线程运行，并提供 empty/pending/settled 确定性截图脚本。
- 独立 macOS PyInstaller spec：生成本机 arm64 `StockWatcher.app`，包含 Retina 配置与
  macOS 留白图标，排除 Windows/TdxQuant 诊断入口；支持 ad-hoc 签名和内部安装验收。
- Mac V1 平台层：Application Support/Logs 路径分离、实际 Keychain backend 校验、系统钥匙串
  文案、单实例、Dock/应用菜单、关闭隐藏/显式退出、右下角多屏弹窗、次要 Notification Center
  和睡眠/网络恢复保护；默认 Tushare 入口延迟加载 Windows TdxQuant 诊断模块。
- 15:30盘后回顾自动生成固定A4纵向三页PDF；App可按日期选择最近31个自然日的报告并下载。
  PDF由本地确定性脚本排版，不依赖AI大模型，不上传行情、候选或凭据。
- 全市场无重叠扫描协调器、15 分钟实时快照环形缓冲、历史分钟预热，以及确定性的
  1/3/5 分钟涨速、突然加速、成交放大、当日前高、三日新高和相对板块强弱。
- 行业/概念 SectorEngine、板块硬门、100 分候选引擎、正式/补位三席、同板块最多两只、
  8 分立即替换/连续三轮替换和三轮新鲜恢复。
- 09:45、14:45 与强异动提醒状态机；强异动每日最多三次、同股五分钟冷却，资金不可用
  时允许以个股+板块触发并标记未确认。
- 不读取命令行 Token 的 30 分钟真实交易时段验证脚本，记录每轮三只、原因、覆盖、重复、
  源年龄、耗时、成功率与 SHA-256。
- 确定性的盘后回顾引擎与工具内总结视图：使用真实日线收盘、前三个交易日背景和行业
  硬门形成市场广度、强势行业与回溯 Top3；不把收盘回放伪装成盘中提醒，也不虚构
  1/3/5 分钟涨速或资金增强。2026-07-29 回放统计 5,516 只有效证券、4,248 只上涨，
  回溯观察为欢乐家、东百集团、国芳集团。
- Windows 0.4.0-alpha.2 的 PyInstaller portable 与 Inno Setup 安装器构建契约；构建产物
  保持仓库外发布，未签名包在受管设备上仍需可信代码签名或管理员允许规则。
- 经 Human Owner 明确授权的 Tushare SDK 原生实时 Provider：固定供应商验证入口、
  800 只批次上限、0.5 秒最小请求间隔、供应商日期/时间来源校验、脱敏全市场 M0
  工具和冻结运行时的最小 SDK 模块收集。2026-07-29 盘后工程探测覆盖 5530/5530，只作为
  `NON_AUTHORITATIVE_ENGINEERING_CHECK`；20/20 盘后交叉样本确认量为“股”、额为“元”，
  交易时段 M0 与候选开放仍待验证。
- StockWatcher 品牌 PNG 与多尺寸 Windows ICO；EXE、安装器和 Qt 窗口使用同一图标。
- Tushare Super/Fast transport、统一响应解析、TLS/超时/重试/限频策略、能力路由、
  归一化严格校验、脱敏 M0 探针，以及 Windows“数据接口”设置界面。
- Windows TQ 只读界面现在始终分开展示连接状态、最近检测、数据门、候选状态和
  当前验证阶段；默认每 60 秒自动执行严格连接检测，并提供“重新连接 TQ”和
  “立即抓取（只读）”按钮。人工抓取只调用官方回环证券列表接口，显式使用整数
  `list_type=0`，仅显示脱敏成败与问题位置，不显示或保存列表正文，也不绕过 M0
  开放候选。
- Windows 自包含 PyInstaller 候选改用与源码便携包相同的严格启动入口，包内携带固定 Python 3.12、PySide6 与运行依赖；冻结运行时不再要求目标机预装 Python 或源码树。
- v0.1 Mac Replay Foundation：跨平台 Python 工程、Provider 协议、Mock/Replay/Synthetic、SQLite WAL/配置与脱敏滚动日志基础，以及确定性测试。
- STOPPED 恢复来源时间门：拒绝并计数不晚于 STOPPED 截止线的延迟样本，防止旧数据重新放行候选。
- v0.2 Mac Local Alpha：固定三只候选、确定性回放提醒与可追溯 SQLite 记录，以及基于 Mock/Replay 的 Mac 本地界面（当前观察、三只提醒、数据中断、详情和历史）。
- v0.3 TdxQuant 前置交付包：官方本机 HTTP/可选 Python 传输、行情归一化、健康与恢复门、Windows 一键入口、脱敏 M0 报告和 PyInstaller/Inno Setup 配置。
- 当前单机内部自用的离线便携候选：ZIP 纳入完整应用树、PySide6 UI 和原生 TdxQuant Preflight；复用目标机已允许且验签通过的官方 Python 3.12/Pythonw，启动前只读检查预置依赖，且仅在严格原生 PASS 合同成立时打开真实诊断界面；不要求管理员权限、不改 PATH、不首次联网安装依赖。
- Windows Codex 直接交接文档：固定 HAZ-526 唯一线性后继、冻结 ZIP/manifest/payload 证据、HAZ-527 的 runner 阻断边界，以及 Human Owner 脱离 Multica 后可复制执行的 Windows 实机提示词。

### Fixed

- 真实 Tushare Pro `trade_cal` 缺供应商生成时间时，不再被一概拒绝；仅受控
  `tushare_15000` 路线的预期 DEGRADED received-fallback 可通过，空响应、STALE/STOPPED、
  非法/越界日期、矛盾开市状态和 schema 变化继续 fail closed。
- 历史回补状态不再在缺少持久化证据时默认声称完成；running/completed/partial/failed
  分开展示，partial 使用真实 settled/unavailable/skipped/pending 数量，内部 reason 映射为
  简洁中文。
- 大于 500 笔的旧 pending 积压不再遮蔽当前档位，单线程队列任务也改为使用实际执行时间，
  避免跨过 15:05 后仍按提交时刻错误延期。
- 生产全市场缓存默认至少 4500 只并要求行业覆盖 95%；日线必须匹配请求交易日，缺最新日线、
  复权变化和当日复牌统一排除，避免截断证券池或机械跳变污染候选。
- 实时扫描拒绝混合日期和日期回退；交易日切换清空盘中价格/成交量、稳定 Top3 与强异动
  基线，强制基础刷新重新读取证券和日线。
- Windows `uv sync` no longer attempts to build the macOS-only PyObjC dependency; the dependency and lockfile now restrict it to Darwin.
- Windows SQLite damage recovery now integrity-checks a same-directory staging copy, preserves `.corrupt` plus old WAL/SHM evidence, and atomically restores only when the live path is replaceable; an external handle now defers recovery without overwriting active bytes.
- Windows Governance artifact upload now follows the actual `0.4.0-alpha.2` portable ZIP name instead of a stale `0.3.0-alpha` path, so a green upload step cannot silently omit the portable package.
- RC4 reliability closure: continuity summaries now report both the longest wall-clock gap and every trading-session gap over 90 seconds, so the normal lunch break cannot hide an afternoon outage.
- A failed concept refresh now keeps the last verified concept memberships in the running process as well as on disk; refreshed industry/trend context is merged with the last-known-good concept map.
- Selection-audit exports now produce real score-order Top20 and fully populated stable Top3 CSV/JSON rows (name, board, score, level, readiness and stability decision), and cache status reads the nested runtime-universe contract correctly.
- Intraday strong-movement popups now name the triggering stock (or trigger count) while preserving the existing readiness, cooldown and daily-limit rules.

- 稳定替换现在只保留席位，不再保留上一轮候选对象；被保留股票的价格、涨幅、评分、等级、
  原因和源时间全部从本轮新鲜合规扫描刷新，本轮缺失、过期或被排除的股票立即退出。
- 全市场扫描逐股排除旧时间或不可用行情，并继续要求新鲜覆盖率不低于99%；少量停牌或久未
  成交证券不再把整轮误判为失败，也不会进入滚动基线或候选。
- macOS 普通前后台切换不再误判为睡眠唤醒；只有真实挂起、网络恢复或超过20秒的事件循环
  停顿才清理旧基线并进入连续三轮恢复门。
- 15:30 盘后回顾先于过期基础缓存刷新执行；普通 Pro 限流不再提前遮蔽总结生成，授权的
  Super 静态兜底会明确标记 `RETROSPECTIVE_ONLY`，不得冒充主路线或盘中 Live。
- macOS 单实例在主进程被异常中断、遗留 Unix socket 时会安全回收无主端点并恢复启动；仍可
  连接的既有实例优先被唤起，不会被替换。
- 数据接口页现在完整显示 Token 输入框、保存提示和带边框的操作按钮；“测试并保存”始终
  清晰可用，未保存 Token 时“重新检测/清除”明确置灰并在保存后可用，避免误以为页面无响应。
- Super GET 请求现在把调用方声明的字段集合显式传为 `fields` 查询参数，避免
  全市场实时探针无条件拉取未使用字段。
- Windows 自包含启动器的严格原生 Preflight 通过后，现在把同一条已验签官方
  终端路径和已验证状态直接传给诊断界面；首次打开不再重复 API 会话，也不会
  因界面侧丢失终端路径而把已通过的现场误标为 `STOPPED`。手动重新连接仍使用
  同一路径重新执行完整 Preflight，候选继续保持 `WARMING` 和关闭。
- Windows 自包含启动器现在优先只读识别当前交互会话中唯一运行、签名有效的
  `TdxW.exe`，因此官方终端安装在自定义目录且卸载注册表不完整时也能启动；
  多实例或签名不匹配仍然 fail-closed，程序不会自动启动终端；验签子进程从
  固定的 Windows PowerShell 内置模块加载签名命令，不再受父进程
  `PSModulePath` 污染影响。
- Windows 现场首个单证券价量调用实测 5.858 秒，默认回环超时由 5 秒调整为有界的 15 秒，避免开盘冷调用被误判为服务中断；交易日历兼容官方 HTTP 返回的 `Date` 键。
- PyInstaller spec 显式收集动态导入的真实 UI，并在冻结运行时跳过仅适用于源码便携包的外部 Python/依赖检查，同时继续执行官方终端签名、严格原生 Preflight、单实例和 fail-closed 门。
- 自包含运行时以包内受限回环 HTTP 客户端满足 `python_client` 检查；不再因为目标机没有额外安装 `tqcenter` Python 模块而把已经通过端口和 API 会话的严格 Preflight 错判为失败。
- 内部便携入口在原生 Preflight 失败后不再自动启动官方 `TdxW.exe`；终端未运行、TQ 未就绪或 Preflight 未通过时只中文 fail-closed，并以离线合同锁定启动链不使用 elevation verb。真实 Windows 普通用户/UAC 结果仍待新冻结 ZIP 独立复验。
- v0.3 板块关系与交易日历改为实际返回带时间、版本和质量元数据的统一 domain 对象；陈旧/中断后的行情必须完成配置数量的恢复预热样本后才重新放行。
- Windows 入口仅选择项目支持的 Python 3.11/3.12，原生命令失败立即返回非零；独立真实 Windows 无终端验收在两个版本上验证安装、导入、CLI、PowerShell、PyInstaller 和 Inno Setup 链路。GitHub Actions 因账户 billing 未启动，不能表述为 CI PASS。
- TdxQuant Preflight 在 API 会话返回供应商错误、意外响应或安全兜底异常时，先写出结构完整的 UTF-8 脱敏 `FAIL` 报告，再保持 PowerShell 非零退出；报告不再包含供应商原始 detail。
- Windows Build 通过临时短盘符把 ISCC、临时文件、备份、替换和最终产物路径全部约束在经典路径预算内，并以可回滚事务成组发布 installer/portable ZIP；Preflight 严格校验 UTF-8、固定 schema/检查集合及重算聚合终态，对子进程启动失败、非零退出、缺失或语义畸形报告统一先原子落盘固定、脱敏的 `FAIL` 报告，再传播失败语义。
- TdxQuant 股票列表的 Preflight 与运行期 Provider 请求显式传递官方整数默认参数 `list_type: 0`，修复本机官方桥对仅含 `market` 的请求返回 `ErrorId=10`、导致 API 会话 fail-closed 的兼容问题。

### Validation

- 2026-07-28 真实普通用户 Windows：当前源码候选工程门为 142 passed、0 skipped，Ruff、Mypy、workspace、Windows package contract 与 diff check 全部通过；自包含 PyInstaller bundle 含 818 个文件、13 个 Qt DLL，归档回读确认包含严格入口、`stock_watcher.ui.app` 与主窗口。EXE 尚未商业代码签名，锁屏后的 Explorer 首次双击验证仍待会话正常解锁。
- 冻结代码候选 `7d5c8b07dd714d4f209528d23074692e8644103c` 已通过独立真实 Windows 无终端工程/打包验证：Python 3.11/3.12 各 74 项测试、PowerShell Setup 与 loopback Preflight 失败闭环、PyInstaller 和 Inno Setup 均通过。该结论不包含真实 TdxQuant、行情、紫黄线、交易时段、通知、多屏或安装卸载体验。

### Pending synchronization

- v0.1/v0.2 与较早 v0.3 前置候选已合入本地 `main`；HAZ-511/512/515/526 的唯一后继通过单一 draft PR #2 的远端 head 交接。PR 尚未合入 `origin/main`，不得称为远端已发布。
- v0.3 已有 Mac 全量回归和较早候选的独立真实 Windows 无终端工程/打包证据；HAZ-527 在下载候选前被 `runner pipe-in` 阻断，因此 HAZ-526 的 Windows 普通用户启动、UAC、真实 TdxQuant Preflight/UI、行情、紫黄线、完整交易时段、通知/多屏和安装卸载体验仍未验证。

## [0.0.0] - 2026-07-22

### Added

- 建立 StockWatcher 私有 GitHub 项目与本地权威目录。
- 安装 Kahlil Project Workflow 文档骨架、领域规则、版本路线与发布治理。
- 导入 V2.0 机器可读规格、锁定项、M0 清单、验收清单、配置/Schema 样例和配图。
- 增加 Bootstrap 文档完整性检查与 GitHub Actions 治理检查。
