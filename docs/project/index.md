# 项目长期事实索引

> 最后更新：2026-07-22
> 这里只放长期为真的事实。单次改动进版本文档，规则和踩坑进 `docs/process/`。

## 项目是什么

- 一句话定位：为内部资深 A 股交易员持续扫描市场，用确定性规则把全市场缩小为三只候选并提供低打扰异动提醒。
- 目标用户 / 场景：张新玲负责最终业务确认；2—3 名内部用户在 Windows 台式机与通达信同时运行，主机常开，iPhone 仅作辅助通知。
- 核心问题：减少人工逐行业、概念和个股持续盯盘的时间，同时保留交易员自行看盘与决策。
- 明确不做：自动买卖、账户/持仓读取、交易密码、下单接口、收益承诺、面向公众发布候选、首版 SaaS/多租户、新闻 AI 和盘中 LLM 选股。
- 产品名：仓库与工程名为 `StockWatcher`；V2.0 原始规格中的业务名为“A股候选观察与异动提醒工具”。

## 业务与参考来源

| 来源 | 拆解结论 | 对项目的约束 |
| --- | --- | --- |
| 张新玲 2026-07-22 需求确认与补充 | 固定三只、板块共振、紫黄线、09:45/14:50、少打扰、内部使用 | `requirements.lock.json` 为锁定业务项，变更需新版本确认 |
| 通达信 TdxQuant 官方能力 | 可能提供实时/历史、板块和批量公式调用，但字段、授权、刷新与紫黄线一致性必须现场证明 | 首版先做 M0；生产主链路禁止 OCR/网页抓取替代 |
| Windows App Notifications | 桌面端可做非抢焦点提醒，但多屏、停留和安装后的实际行为需 Windows 验证 | UI 验收不能只靠单元测试 |
| Bark | 可作为可替换的 iPhone 辅助通道 | 手机失败不得阻塞桌面，设备密钥不得入库 |

官方链接与更完整来源见 `docs/reference/v2.0/SPEC_V2.0_AGENT.md` 附录 G。当前没有可复用历史正反案例；Replay/Synthetic 与试用期采集是既定路线。

## 技术栈与运行

| 项 | 内容 |
| --- | --- |
| 语言 | 计划 Python 3.11/3.12；最终版本受现场 TdxQuant 支持范围约束 |
| 桌面 UI | 计划 PySide6；Windows 右下角、不抢焦点、常驻托盘 |
| 并发 | provider、engine、UI、notification 责任隔离；具体进程模型在 v0.2 验证 |
| 数据库 | SQLite WAL；分钟数据可按验证结果使用 Parquet / DuckDB |
| 配置 | YAML + Pydantic；锁定规则、软参数、用户设置和运行环境分层 |
| 测试 | pytest + ReplayProvider + SyntheticScenarioBuilder；真实数据另做 M0/影子验证 |
| 打包 | 计划 PyInstaller one-folder + Inno Setup，目标 Windows x64 |
| 部署 | 无生产部署；每位内部用户独立本地安装，首版不建账号系统 |
| 当前验证 | `python3 scripts/validate_workspace.py`、`git diff --check` |

## 计划模块表

| 模块 | 职责 | 计划路径 | 依赖 | 被谁消费 |
| --- | --- | --- | --- | --- |
| domain | 统一证券、行情、板块、资金、候选、提醒和健康对象 | `src/stock_watcher/domain/` | 无供应商字段依赖 | providers、engine、storage、UI |
| providers | 通达信、Replay 和未来合法数据源适配；归一化字段与质量 | `src/stock_watcher/providers/` | domain、现场 SDK | engine、health、M0 工具 |
| engine | 股票池、价格、板块、资金、三日、排名和提醒策略 | `src/stock_watcher/engine/` | domain、providers 输出、配置 | desktop、storage、summary |
| desktop | 主窗口、弹窗、托盘、设置、历史与反馈 | `src/stock_watcher/ui/` | engine 事件、storage | 内部用户 |
| notifications | Bark 等可替换辅助通道 | `src/stock_watcher/notifications/` | AlertBatch、密钥存储 | iPhone |
| storage | SQLite、repository、缓存、配置版本 | `src/stock_watcher/storage/` | domain、配置 | engine、UI、jobs |
| jobs / health | 总结、备份、看门狗、恢复与指标 | `src/stock_watcher/jobs/`、`health/` | providers、storage | UI、运维 |
| tools | M0 探针、回放工具 | `tools/` | providers、domain | 开发与测试 |

以上是规格建议的依赖方向，不代表代码目录已经存在。首次建立代码结构时必须在活跃版本中验证后更新本表。

## 相关文档

- 锁定业务项：`docs/reference/v2.0/requirements.lock.json`
- 完整规格：`docs/reference/v2.0/SPEC_V2.0_AGENT.md`
- 版本路线：`docs/visions/README.md`
- 高风险边界：`docs/process/boundaries.md`

## 待确认

- 通达信现场版本、安装方式、SDK/Python 兼容版本和账号授权范围（M0）。
- 紫色超大单、黄色大单的准确指标名、输出字段、单位、累计方式和历史可取性（M0）。
- 全市场批量、板块成分、重连与一个完整交易时段的性能/稳定性（M0）。
- Bark 是否适合现场网络；不适合时选择其他合规通知通道（v0.3）。
