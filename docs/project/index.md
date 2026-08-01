# 项目长期事实索引

> 最后更新：2026-07-30
> 这里只放长期为真的事实。单次改动进版本文档，规则和踩坑进 `docs/process/`。

## 项目是什么

- 一句话定位：为内部资深 A 股交易员持续扫描市场，用确定性规则把全市场缩小为三只候选并提供低打扰异动提醒。
- 当前用户 / 场景：Human Owner 先在 macOS 本机内部验证 V1；Windows 后续只同步共享修复并
  单独完成真实验收。Mac 结果不能替代 Windows 证据。
- 核心问题：减少人工逐行业、概念和个股持续盯盘的时间，同时保留交易员自行看盘与决策。
- 明确不做：自动买卖、账户/持仓读取、交易密码、下单接口、收益承诺、面向公众发布候选、首版 SaaS/多租户、新闻 AI 和盘中 LLM 选股。
- 产品名：仓库与工程名为 `StockWatcher`；V2.0 原始规格中的业务名为“A股候选观察与异动提醒工具”。

## 业务与参考来源

| 来源 | 拆解结论 | 对项目的约束 |
| --- | --- | --- |
| Human Owner 2026-07-29 最终 V1 基线 | 15000 积分单 Token、全市场实时 Top3、09:45/14:45/强异动、资金可降级 | 优先级高于旧 V2.0 中冲突的 14:50、紫黄线和手机范围 |
| 张新玲 2026-07-22 需求确认与补充 | 固定三只、板块共振、紫黄线、09:45/14:50、少打扰、内部使用 | `requirements.lock.json` 为锁定业务项，变更需新版本确认 |
| Hazel Kahlil 2026-07-22 环境确认 | 当前只有 Mac，日常迭代改为本地优先 | 先做跨平台 Mock/Replay 基础；Windows/通达信证据延后到独立版本 |
| Human Owner 2026-07-30 Mac-first 决策 | 先在 Mac 验证共享 Tushare 主路线、真实 Top3 与系统适配；共享修复后同步回 Windows | Windows `rate_limited` 失败和 0 轮扫描继续保留为 `FAIL`，不得由 Mac 结果覆盖 |
| v0.3.1 数据路线决策 | 普通/历史/板块固定使用 `fastapic` Pro 代理，实时固定使用 SDK `realtime_quote(src="sina")` | Mac 与 Windows 共用 Provider、模型、归一化、算法、SQLite 和核心 UI；平台差异隔离 |
| 通达信 TdxQuant 官方能力 | Windows 已验证部分实时、列表、日线、板块和交易日历能力，但分钟历史与可信秒级源时间仍未通过 | 保留为可选诊断和未来资金字段探索，不再阻塞正常启动 |
| Windows App Notifications | 桌面端可做非抢焦点提醒，但多屏、停留和安装后的实际行为需 Windows 验证 | UI 验收不能只靠单元测试 |

官方链接与更完整来源见 `docs/reference/v2.0/SPEC_V2.0_AGENT.md` 附录 G。当前没有可复用历史正反案例；Replay/Synthetic 与试用期采集是既定路线。

## 技术栈与运行

| 项 | 内容 |
| --- | --- |
| 当前开发机 | macOS；本地开发、工程门、Keychain、通知、安装和真实数据都以本机证据为准，且需明确标为 Mac |
| 语言 | Python 3.11/3.12；Tushare transport、解析、归一化与业务模型保持跨平台 |
| 桌面 UI | PySide6；数据接口设置支持系统安全存储、先测试后切换和状态展示 |
| 并发 | 单扫描协调器禁止重叠；provider、engine、UI 和持久化责任隔离 |
| 数据库 | SQLite WAL v3；候选批次及三只明细原子保存 |
| 配置 | YAML + Pydantic；锁定规则、软参数、用户设置和运行环境分层 |
| 测试 | pytest + Replay/Synthetic；默认不需要 Token；真实 30 分钟使用独立脱敏脚本 |
| 打包 | PyInstaller + Inno Setup；正常入口只使用 Tushare 单 Token，TdxQuant 为高级诊断 |
| 部署 | 仅本机内部使用；不接交易账户，不自动下单，不执行发布或远端同步 |
| 当前验证 | Mac原生实时全市场七批、连续双次手动Top3、14:45固定触发与延迟兜底、算法/UI/SQLite/离线门及最终arm64 `.app`安装已建立；新鲜固定时点Top3、15:30准点报告、真实恢复会话与Windows验收仍是严格门 |

## 计划模块表

| 模块 | 职责 | 计划路径 | 依赖 | 被谁消费 |
| --- | --- | --- | --- | --- |
| domain | 统一证券、行情、板块、资金、候选、提醒和健康对象 | `src/stock_watcher/domain/` | 无供应商字段依赖 | providers、engine、storage、UI |
| providers | Tushare 兼容 HTTP 主路线、Replay 与可选 TdxQuant 诊断；归一化字段、时间戳与质量 | `src/stock_watcher/providers/` | domain、HTTPS 数据接口 | engine、health、M0 工具 |
| engine | 股票池、价格、板块、资金、三日、排名和提醒策略 | `src/stock_watcher/engine/` | domain、providers 输出、配置 | desktop、storage、summary |
| desktop | 主窗口、弹窗、托盘、设置、历史、盘后回顾PDF与反馈 | `src/stock_watcher/ui/` | engine 事件、storage | 内部用户 |
| storage | SQLite、repository、缓存、配置版本 | `src/stock_watcher/storage/` | domain、配置 | engine、UI、jobs |
| jobs / health | 总结、备份、看门狗、恢复与指标 | `src/stock_watcher/jobs/`、`health/` | providers、storage | UI、运维 |
| tools | M0 探针、回放工具 | `tools/` | providers、domain | 开发与测试 |

V1 已实现 `domain`、`providers`、`runtime`、`engine`、`storage` 和跨平台 PySide6 UI 主链路，
并提供不冒充盘中证据的真实收盘回顾；Mac真实交易时段全市场与内部arm64 `.app`已有证据，
但新鲜固定时点Top3、15:30准点报告和真实恢复会话仍未完成，Windows真实验收仍未通过。

## 相关文档

- 锁定业务项：`docs/reference/v2.0/requirements.lock.json`
- 完整规格：`docs/reference/v2.0/SPEC_V2.0_AGENT.md`
- 版本路线：`docs/visions/README.md`
- 高风险边界：`docs/process/boundaries.md`

## 待确认

- 当前单 Token 对普通/历史、板块、分钟和原生实时方法的交易时段权限、限频与稳定性。
- 全市场实时快照、最近三日分钟、行业/概念板块与可信源时间的连续 30 分钟证据。
- moneyflow 或服务商资金字段是否有可靠盘中更新；只有日级时不得冒充盘中增强。
- Mac 全市场批量、板块成分、重连与完整交易时段性能/稳定性，以及 Windows 后续单独的
  真实验收；Mac/CI 结果不能替代 Windows 证据。
- 独立资金字段通过前保持“资金未确认”，候选仍必须生成。
