# 项目长期事实索引

> 最后更新：2026-07-29
> 这里只放长期为真的事实。单次改动进版本文档，规则和踩坑进 `docs/process/`。

## 项目是什么

- 一句话定位：为内部资深 A 股交易员持续扫描市场，用确定性规则把全市场缩小为三只候选并提供低打扰异动提醒。
- 当前用户 / 场景：Hazel Kahlil 使用 Mac 开发且没有 Windows 电脑；张新玲仍是原始业务规格中的确认人。原规格设想的 2—3 名 Windows + 通达信内部用户场景尚未获得真实环境验证。
- 核心问题：减少人工逐行业、概念和个股持续盯盘的时间，同时保留交易员自行看盘与决策。
- 明确不做：自动买卖、账户/持仓读取、交易密码、下单接口、收益承诺、面向公众发布候选、首版 SaaS/多租户、新闻 AI 和盘中 LLM 选股。
- 产品名：仓库与工程名为 `StockWatcher`；V2.0 原始规格中的业务名为“A股候选观察与异动提醒工具”。

## 业务与参考来源

| 来源 | 拆解结论 | 对项目的约束 |
| --- | --- | --- |
| 张新玲 2026-07-22 需求确认与补充 | 固定三只、板块共振、紫黄线、09:45/14:50、少打扰、内部使用 | `requirements.lock.json` 为锁定业务项，变更需新版本确认 |
| Hazel Kahlil 2026-07-22 环境确认 | 当前只有 Mac，日常迭代改为本地优先 | 先做跨平台 Mock/Replay 基础；Windows/通达信证据延后到独立版本 |
| v0.3.1 数据路线决策 | 默认主数据源切换为跨平台 Tushare 兼容 HTTPS；超级接口为主，快速接口仅在同口径实测后按能力加速 | Windows 先完成数据闸门，Mac 复用同一 Provider、模型和业务核心 |
| 通达信 TdxQuant 官方能力 | Windows 已验证部分实时、列表、日线、板块和交易日历能力，但分钟历史与可信秒级源时间仍未通过 | 保留为可选诊断和未来资金字段探索，不再阻塞正常启动 |
| Windows App Notifications | 桌面端可做非抢焦点提醒，但多屏、停留和安装后的实际行为需 Windows 验证 | UI 验收不能只靠单元测试 |
| Bark | 可作为可替换的 iPhone 辅助通道 | 手机失败不得阻塞桌面，设备密钥不得入库 |

官方链接与更完整来源见 `docs/reference/v2.0/SPEC_V2.0_AGENT.md` 附录 G。当前没有可复用历史正反案例；Replay/Synthetic 与试用期采集是既定路线。

## 技术栈与运行

| 项 | 内容 |
| --- | --- |
| 当前开发机 | Windows；本地开发、工程门和真实数据闸门均以本机证据为准 |
| 语言 | Python 3.11/3.12；Tushare transport、解析、归一化与业务模型保持跨平台 |
| 桌面 UI | PySide6；数据接口设置支持系统安全存储、先测试后切换和状态展示 |
| 并发 | provider、engine、UI、notification 责任隔离；具体进程模型在 v0.2 的 Mac Alpha 验证 |
| 数据库 | SQLite WAL；分钟数据可按验证结果使用 Parquet / DuckDB |
| 配置 | YAML + Pydantic；锁定规则、软参数、用户设置和运行环境分层 |
| 测试 | pytest + ReplayProvider + SyntheticScenarioBuilder；真实数据另做 M0/影子验证 |
| 打包 | PyInstaller + Inno Setup；v0.3.1 正常入口不依赖通达信，TdxQuant 使用独立诊断入口 |
| 部署 | 仅本机内部使用；不接交易账户，不自动下单，不执行发布或远端同步 |
| 当前验证 | Tushare 路线的离线解析、错误处理、归一化、路由、凭据替换与包合同已建立；真实凭据 M0、30 分钟交易时段稳定性和真实候选闭环仍是严格门 |

## 计划模块表

| 模块 | 职责 | 计划路径 | 依赖 | 被谁消费 |
| --- | --- | --- | --- | --- |
| domain | 统一证券、行情、板块、资金、候选、提醒和健康对象 | `src/stock_watcher/domain/` | 无供应商字段依赖 | providers、engine、storage、UI |
| providers | Tushare 兼容 HTTP 主路线、Replay 与可选 TdxQuant 诊断；归一化字段、时间戳与质量 | `src/stock_watcher/providers/` | domain、HTTPS 数据接口 | engine、health、M0 工具 |
| engine | 股票池、价格、板块、资金、三日、排名和提醒策略 | `src/stock_watcher/engine/` | domain、providers 输出、配置 | desktop、storage、summary |
| desktop | 主窗口、弹窗、托盘、设置、历史与反馈 | `src/stock_watcher/ui/` | engine 事件、storage | 内部用户 |
| notifications | Bark 等可替换辅助通道 | `src/stock_watcher/notifications/` | AlertBatch、密钥存储 | iPhone |
| storage | SQLite、repository、缓存、配置版本 | `src/stock_watcher/storage/` | domain、配置 | engine、UI、jobs |
| jobs / health | 总结、备份、看门狗、恢复与指标 | `src/stock_watcher/jobs/`、`health/` | providers、storage | UI、运维 |
| tools | M0 探针、回放工具 | `tools/` | providers、domain | 开发与测试 |

以上是规格建议的依赖方向。v0.1 已实现 `domain`、`providers`、`storage` 和 `config` 的最小可回放基础；engine、desktop、notifications 与 jobs/health 仍由后续版本承接。

## 相关文档

- 锁定业务项：`docs/reference/v2.0/requirements.lock.json`
- 完整规格：`docs/reference/v2.0/SPEC_V2.0_AGENT.md`
- 版本路线：`docs/visions/README.md`
- 高风险边界：`docs/process/boundaries.md`

## 待确认

- Tushare 超级/快速接口的新换发凭据、权限、到期、限频与真实响应能力。
- 全市场实时快照、最近三日 1/5 分钟、板块与可信源时间的交易时段 M0。
- 通达信紫色超大单、黄色大单的准确指标名、输出字段、单位、累计方式和历史可取性（Windows M0）；`Zjl`/`Zjl_HB` 不得静默替代。
- 全市场批量、板块成分、重连与完整交易时段性能/稳定性；Mac/CI 结果不能替代 Windows 证据。
- 独立资金字段 M0 通过前，资金模块保持 `unavailable`。
- Bark 是否适合现场网络；不适合时选择其他合规通知通道（v0.4）。
