# 项目长期事实索引

> 最后更新：2026-07-23
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
| 北京沃远数据科技有限公司 Tushare Pro | HAZ-403 查证结论为 `PASS_WITH_LIMITS`：可作为首个 Mac 真实行情 M0 候选，但不是生产接入 PASS | 仅在供应商正式支持的 HTTPS POST/JSON、书面授权和真实环境证据齐备后运行受控 M0；否则保持 `NO-GO for implementation` |
| 通达信 TdxQuant 官方能力 | 可能提供实时/历史、板块和批量公式调用，但字段、授权、刷新与紫黄线一致性必须现场证明 | 完整真实数据路线必须经过 v0.3 M0；生产主链路禁止 OCR/网页抓取替代 |
| Windows App Notifications | 桌面端可做非抢焦点提醒，但多屏、停留和安装后的实际行为需 Windows 验证 | UI 验收不能只靠单元测试 |
| Bark | 可作为可替换的 iPhone 辅助通道 | 手机失败不得阻塞桌面，设备密钥不得入库 |

官方链接与更完整来源见 `docs/reference/v2.0/SPEC_V2.0_AGENT.md` 附录 G。当前没有可复用历史正反案例；Replay/Synthetic 与试用期采集是既定路线。

## 技术栈与运行

| 项 | 内容 |
| --- | --- |
| 当前开发机 | Mac；日常运行、测试和本地 Git 提交均在本机完成 |
| 语言 | Python 3.11/3.12；v0.3 在共享 Provider/domain 契约上分别验证 Mac/Tushare Pro 与 Windows/TdxQuant |
| 桌面 UI | 计划 PySide6；v0.2 先验证 Mac 跨平台原型，Windows 右下角/多屏/托盘行为留到真实环境 |
| 并发 | provider、engine、UI、notification 责任隔离；具体进程模型在 v0.2 的 Mac Alpha 验证 |
| 数据库 | SQLite WAL；分钟数据可按验证结果使用 Parquet / DuckDB |
| 配置 | YAML + Pydantic；锁定规则、软参数、用户设置和运行环境分层 |
| 测试 | pytest + ReplayProvider + SyntheticScenarioBuilder；真实数据另做 M0/影子验证 |
| 打包 | v0.1/v0.2 不做正式安装包；v0.3 只做双路线数据与共享核心闸门，不把任一路线的 M0 结果冒充另一平台的安装或运行证据 |
| 部署 | 当前只有 Mac 本地开发环境，无生产部署；v0.3 同时保留 Mac/Tushare Pro 与 Windows/TdxQuant 路线，分别取得授权和真实环境结论 |
| 当前验证 | v0.2 已在 Mac + Mock/Replay 下本地收口；HAZ-403 对 Tushare Pro 仅为 `PASS_WITH_LIMITS` 且真实接入仍为 `NO-GO for implementation`；Windows/TdxQuant 真实验证仍未进行 |

## 计划模块表

| 模块 | 职责 | 计划路径 | 依赖 | 被谁消费 |
| --- | --- | --- | --- | --- |
| domain | 统一证券、行情、板块、资金、候选、提醒和健康对象 | `src/stock_watcher/domain/` | 无供应商字段依赖 | providers、engine、storage、UI |
| providers | Tushare Pro、TdxQuant、Replay 等获授权数据源适配；归一化字段与质量 | `src/stock_watcher/providers/` | domain、获授权 API/现场 SDK | engine、health、M0 工具 |
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

- Mac/Tushare Pro 路线：供应商是否正式支持 HTTPS POST/JSON，以及账号、套餐、价格、有效期、内部展示、历史与派生结果、日志/缓存、备份/删除和到期处置的书面授权范围（M0）。
- Windows/TdxQuant 路线：取得合规 Windows 环境、书面授权，并确定现场版本、安装方式、SDK/Python 兼容版本和账号授权范围（HAZ-405）。
- 通达信紫色超大单、黄色大单的准确指标名、输出字段、单位、累计方式和历史可取性（Windows M0）；Tushare 字段不得沿用该命名。
- 两条路线各自的全市场批量、板块成分、重连与完整交易时段性能/稳定性；一条路线的结果不能替代另一条路线证据。
- 独立资金字段 M0 通过前，资金模块保持 `unavailable`。
- Bark 是否适合现场网络；不适合时选择其他合规通知通道（v0.4）。
