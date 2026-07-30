# v0.4.2-macos-v1-port：Mac V1 真实数据与平台适配

> 状态：进行中（已完成离线实现与系统 Keychain 实测；Primary 凭据已安全保存，供应商文档
> SDK 路径的轻量连接已通过，等待交易时段连续实测）
>
> 创建：2026-07-30
>
> 冻结业务基线：`5b20b707e83baa16b1486894f8e53f343830d67c`
>
> 共享连接返修：`74b4840d25766097a2c88e502983b375bc80c7d6`、
> `d1bef74297722535fa6c96911672f473ea5f477a`、
> `21ad028541e2b40ae72b7152a865b080983a9fb4`
>
> 共享产品增量：`6309377187d26c46f1994ce201c16693a3caca75`
> （盘中手动 Top3、自动固定提醒回归、15:30 全市场盘后回顾；可后续独立同步 Windows）
>
> 工作分支：`feat/macos-v1-port`

## 决策与证据边界

Human Owner 于 2026-07-30 决定 Mac 先行：在 macOS 上使用与 Windows 相同的 Tushare
核心路线、共享候选逻辑和锁定产品规则，先完成连接门返修、真实全市场 Top3 和桌面验证，
然后把**共享提交**同步给 Windows。

这不改变 Windows 真实验收事实：冻结 Windows 基线在统一 Token 连接校验阶段收到
`rate_limited`，完整实时扫描轮次和真实 Top3 均为 0，结论继续为 `FAIL`。本版本任何
Mac 数据、截图、`.app` 或测试都不能作为 Windows 通过、Windows 通知、Windows 安装或
TdxQuant/M0 通过证据。

## 范围

### 已实施的 Mac 专属层

- [x] 数据/配置使用 `~/Library/Application Support/StockWatcher`，日志使用
  `~/Library/Logs/StockWatcher`，报告保留在 Application Support 的 `reports/`。
- [x] macOS 仅接受实际 `keyring.backends.macOS` Keychain backend；设置页显示
  “系统钥匙串”，并且 Token 不进入 YAML、SQLite、日志、源码、截图或 Git。
- [x] 主窗口关闭时隐藏并继续扫描；Dock/应用激活恢复窗口；应用菜单提供“显示主窗口”、
  “数据接口”和“退出 StockWatcher”。
- [x] 使用 `QLocalServer/QLocalSocket` 保证单实例，第二个进程只请求唤起已有窗口。
- [x] 保留 Qt 右下角三行弹窗；Notification Center 仅为次要 best-effort 通知，权限拒绝
  或 Focus 不影响应用内弹窗；默认优先主窗口所在显示器右下角。
- [x] 睡眠/唤醒和网络恢复清除滚动基线、取消在途扫描并进入 WARMING；连续三轮新鲜完整
  数据前不形成新候选。网络断开时停止定时扫描，保留旧三只但不产生新结果。
- [x] 默认 Mac 入口延迟加载 TdxQuant 诊断代码，避免普通 Tushare Mac 进程加载 Windows
  Tdx 依赖；Windows PowerShell/VBS/Inno/TdxQuant 文件仍保留，后续 Mac 打包必须排除。
- [x] 内部测试入口：`scripts/install_macos_dev_app.py` 可生成
  `~/Applications/StockWatcher Dev.app`，使用 macOS 留白图标并从当前工程启动真实 UI，方便
  Human Owner 打开“数据接口”手动输入 Token；它不是最终 PyInstaller 发布包。
- [x] 主界面布局调整：压缩顶部状态和连接说明区，把候选列表/空候选占位作为主视觉区域，
  避免接口状态抢占 3 只观察股票的位置。
- [x] 主界面提供醒目的“立即获取最新3只”：盘中点击后强制执行一次最新全市场扫描，
  成功即更新主界面并弹出同一组三只；手动查看不写自动提醒事件、不占用 09:45、14:45
  或强异动每日限额，数据失败时保留上次结果且不产生新弹窗。
- [x] 09:45、14:45 继续由交易时段调度器自动触发；扫描跨越固定时点时补触发，同一分钟
  和应用重启后均按当日持久化记录去重。
- [x] 15:30 自动生成全市场盘后回顾：包含涨跌家数、上涨比例、中位涨幅、强势行业、
  收盘观察 Top3、自动提醒次数、资金与数据限制；手动查看不计入提醒限额。结果写入 SQLite
  总结页，并原子生成
  `reports/YYYY-MM-DD-A股盘后回顾.json`、`.md` 与固定 A4 纵向三页 `.pdf`。普通菜单
  “盘后回顾与PDF”可选择今天或最近31个自然日的历史报告，并通过 macOS 保存对话框下载；
  内部报告与 SQLite 总结超出31日自动清理，用户已下载的外部副本不受影响。
- [x] PDF 使用本地确定性脚本排版，不接 AI 大模型、不向外部服务上传行情、候选或凭据；
  固定三页分别为市场全景、收盘观察 Top3、运行回顾与数据说明。
- [x] 历史分钟继续独立显示能力状态，但不可用时不再阻塞核心实时 1/100/300/800
  渐进检查、全市场扫描和 Top3；资金未知同样不阻塞。

### 仍待真实环境验证

- [x] Primary 凭据已通过 macOS “系统钥匙串”保存；不进入命令、配置、日志、截图或 Git。
- [ ] 非交易时段：轻量保存、静态/历史/板块及单只原生实时结构。
- [ ] 交易时段：1/100/300/800 批次递进、连续全市场扫描、覆盖率、source age、价格量额
  推进、429、错误、p50/p95 和恢复三轮指标。
- [ ] 真实 Top3：板块硬门、同板块最多两只、强/中/近、补位观察、稳定替换、资金未确认
  不阻塞。
- [ ] 至少一个真实 09:45 或 14:45 固定弹窗；自然强异动未出现时仅以 Replay 证明逻辑并
  如实标记“未观察到 live”。
- [ ] 30 天历史和 15:30 总结的真实数据证据。
- [ ] Retina、多屏、Dock 恢复、睡眠/网络恢复的人工图形会话验收。
- [ ] 真实数据与 Top3 通过后新增独立 Mac PyInstaller spec，构建本机架构 `.app`，可做
  ad-hoc 签名，并在全新复制目录验证 Keychain、数据库、显式退出与第二次启动。

## 数据与安全契约

- 普通、历史和板块固定走 `https://fastapic.stockai888.top`；主实时固定走
  `tushare.realtime_quote(..., src="sina")`，原生校验地址为
  `https://realtime.stockai888.top`。
- 普通 Pro 遵循供应商文档的 SDK 调用路径：`POST /<api_name>` 并带
  `ts_type_name`；为避免 `ts.set_token()` 写入 `~/tk.csv`，应用以 Keychain 中的 Token
  在内存中执行同一 SDK wire contract，不使用环境变量或文件兜底。
- `rt_k` 不进入 15000 积分主实时路线；资金无可靠盘中数据时显示“资金未确认”，不得以
  日级 moneyflow 冒充盘中大单/超大单，也不得阻塞 Top3。
- 应用级共享请求预算的默认请求起点间隔为 1 秒；429 保留 Token，遵守 `Retry-After`，
  无值时 60 秒冷却后从失败能力继续检查。
- 共享返修仅位于 `74b4840d…`、`d1bef742…` 与 `21ad028…`；它们都不含 macOS 路径、
  Keychain、生命周期、通知或打包代码，可独立同步到 Windows。

## 当前可复现实测

- 2026-07-30：实际 backend 为 `keyring.backends.macOS.Keyring`；使用临时非敏感条目
  完成写入、读取、删除 round-trip，结果 PASS，未保留条目。
- 2026-07-30：旧的根路径普通 Pro `trade_cal` 轻量实测曾返回 `rate_limited`，凭据保留，
  未将该结果写成 Token 无效。随后按供应商文档切换 SDK 路径，在 macOS Keychain 中读取
  Primary Token 进行一次 `POST /trade_cal` 轻量实测，HTTP 200、8 条；这只证明基础连接，
  不等同于全市场扫描或交易时段验收。
- 2026-07-30：按文档原生 `realtime_quote(src="sina")` 单证券结构实测返回 HTTP 200、1 条，
  且有代码、价格、昨收、量、额和供应商时间字段；这只证明接口结构，不是交易时段全市场
  扫描或真实 Top3。
- 2026-07-30：单实例 guard 已覆盖异常中断后遗留 Unix socket 的恢复：仅在
  `ServerNotFoundError` 或 `ConnectionRefusedError` 时回收端点；可连接的主进程仍优先被
  唤起，避免第二次启动永久失败。
- 2026-07-31：新增手动 Top3、固定提醒回归、完整盘后回顾、PDF下载和60秒失败重试后，
  离线门重新通过：`uv sync --all-groups --frozen`、`uv lock --check`、全量 pytest
  （270 passed、20 skipped、2 deselected）、Ruff、Mypy（96 个源文件）、workspace 和
  `git diff --check`；离屏 Replay 五状态 PNG 5/5 重新生成，SQLite WAL/备份/回滚/迁移
  定向回归 5/5 通过。视觉复核确认主界面候选区仍为主体，“立即获取最新3只”为左侧宽主按钮。
- 2026-07-31：使用7月30日真实静态收盘数据生成固定A4纵向三页PDF；`pdfinfo`确认3页、
  A4、无表单/JavaScript，逐页PNG视觉检查确认中文、Top3卡片和表格无截断。报告 SHA-256
  为 `39d16b4ef19169243bb25342a178c769895cbe77b081f5c38f40eabcd1e47ffd`。
  App离屏实测显示日期选择器、7月30日内容和“下载 PDF”按钮完整可见。该报告仍是
  Super高级诊断生成的真实静态收盘回顾，不构成盘中Live、固定提醒或Windows验收。
- 2026-07-31 00:43 CST：当前为非交易时段，因此本轮没有把离线测试、模拟 Provider 或
  盘后日线回顾冒充实时 Top3；下一次 live 仍必须在交易时段使用系统钥匙串中的 Primary
  凭据验证。
- 2026-07-30 15:33 CST：盘后主路线复核返回 `rate_limited`；15:43–15:44 CST 允许的
  Super 静态高级诊断返回 `empty_data`。两者均未生成报告、Top3 或当日日线覆盖，不构成
  实时、主路线、盘中 M0 或 Windows 证据。
- 2026-07-30 23:38–23:58 CST：按供应商文档直接使用 Tushare 1.4.29 SDK 复核。
  `trade_cal` 成功返回 46 条并确认 7 月 30 日开市；普通 `stock_basic` 原始 HTTP 响应持续为
  429（`请求次数超限（每分钟最多120次）`）。SDK 的 `DataApi.query()` 会因
  `requests.Response` 对 429 求值为 false 而静默返回空 DataFrame，因此此前“0 行”不是收盘
  数据不存在。停止运行中的 Dev App 后，已授权 Super 静态接口成功返回 5530 条股票资料和
  最近四个交易日 5523–5528 条日线，生成真实收盘回顾 Top3：金徽酒、舍得酒业、美邦股份。
  证据见 `post-close-review-20260730-super-diagnostic.json` 和
  `2026-07-30-盘后回顾-Super静态诊断.md`；它仍只属于高级静态诊断，不构成普通主路线、
  盘中实时、固定提醒或 Windows 验收通过。
- 实时验证脚本只读取 Primary 凭据，普通 Pro 与原生实时注入同一个 1 秒应用级预算；报告逐项
  列出 1/100/300/800 能力状态、429/错误和全市场价格/量额的**聚合**推进，不保存原始市场
  行、Token 或旧 Fast 凭据。盘后脚本同样只读取 Primary（高级 Super 诊断仅在显式参数与独立
  凭据同时存在时可用，绝不构成主路线验收）。
- 离线工程门、Replay 五状态、SQLite 和全部 Mac 专属回归将在本分支最终提交前重新完整
  记录；它们只证明 macOS 本地工程行为，不证明真实 Token、真实行情或 Windows。

## 唯一下一步

下一交易日 09:25 启动已安装的 Mac Dev App，使用系统钥匙串中的 Primary 凭据、供应商
文档 SDK 路径和 1 秒共享预算受控执行主路线实时验证；先完成 1/100/300/800、连续三轮
恢复和手动 Top3，再覆盖 09:45 固定弹窗。普通 Pro 通过完整真实数据门后，才可进入最终
PyInstaller `.app` 构建与最终交接包。
