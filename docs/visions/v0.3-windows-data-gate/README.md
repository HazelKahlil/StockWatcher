# v0.3-windows-data-gate：双路线真实数据闸门

> 状态：活跃（双路线 M0；真实接入仍为 `NO-GO for implementation`）
> 创建：2026-07-22 ｜ 激活：2026-07-23 ｜ PASS/PASS_WITH_LIMITS 后计划 tag：`v0.3.0`

目录名保留最初的 Windows 数据闸门命名；本版本现按 Human Owner 批准的双路线决策执行，不以 Mac 路线替代 Windows 路线。

## 权威结论

- v0.2 已在本地 `main` 完成 Mac + Mock/Replay 范围，GitHub 尚未同步；v0.3 接手真实数据、授权和共享核心验证。
- HAZ-403 的查证结论是 `PASS_WITH_LIMITS`：北京沃远数据科技有限公司 Tushare Pro 是首个 Mac M0 候选，不是生产接入 PASS。官方文档/SDK 仍存在明文 HTTP 入口；正式 HTTPS 支持、书面授权和真实环境证据未齐前，保持 `NO-GO for implementation`。
- Mac 路线只能通过供应商正式支持的 HTTPS POST/JSON 运行真实行情 M0。若供应商不能正式确认 HTTPS，该首选路线直接判定 FAIL；真实 token 在任何情况下都不得经 HTTP 传输。
- Windows 路线继续使用官方通达信 TdxQuant，等待合规 Windows、书面授权和 HAZ-405 现场 M0。Mac/Tushare Pro 证据不能替代 Windows、TdxQuant、通达信紫黄线、Windows 通知或安装包证据。

## 双路线范围

| 路线 | 数据源与目的 | 当前门禁 |
| --- | --- | --- |
| Mac | 北京沃远数据科技有限公司 Tushare Pro；验证真实行情 M0 与共享核心 | 仅 HAZ-403 `PASS_WITH_LIMITS`；正式 HTTPS、书面授权与受控真实环境未齐，`NO-GO for implementation` |
| Windows | 官方通达信 TdxQuant；验证原规格行情、板块、紫黄线、批量、重连及现场授权 | 等待合规 Windows、书面授权和 HAZ-405 现场 M0 |

两条路线共用 Provider/domain 对象、`source_ts` / `received_ts`、provider/config 版本、Asia/Shanghai 时区、健康状态、去重和 Replay 契约。供应商字段必须先归一化，engine 不直接读取供应商字典键。

Tushare 字段不得命名或展示为通达信紫黄线，也不得据此声称通达信资金口径已验证。资金模块继续显示 `unavailable`，直至独立资金字段 M0 通过。

## 账号、网络与秘密开工门

- [x] Human Owner 已批准双路线验证方向；该批准不等于供应商授权、付费或生产接入许可。
- [ ] 供应商以正式渠道确认 Mac M0 所用 HTTPS POST/JSON 是受支持入口；无法确认则 Mac 首选路线 FAIL，不运行真实 token。
- [ ] Human Owner 提供自有且已授权的最小权限账号，并书面确认套餐、价格、有效期和使用范围；技术可调用不等于获许可。
- [ ] Tushare token 只从 `TUSHARE_TOKEN` 环境变量读取，不进入 issue、评论、日志、Git、SQLite、截图或示例配置；任何输出均不得回显 token。
- [ ] Agent 不注册账号、不购买套餐、不接受供应商条款、不调用下单或交易账户能力。
- [ ] Windows 路线取得合规 Windows、官方 TdxQuant、书面授权及 HAZ-405 现场条件。
- [ ] 执行者已读取 `rules/data.md`、`rules/security.md`、`boundaries.md` 与 `docs/reference/v2.0/m0_checklist.md`。

两条路线的门禁独立判定：Mac 路线须先具备正式 HTTPS、书面授权和受控真实环境；Windows 路线须先具备合规 Windows、官方 TdxQuant、书面授权和 HAZ-405 现场条件。对应路线的门未齐前，不运行该路线真实数据探针；门齐后也只先运行受控 M0。生产 Provider 仍须等待该路线的真实环境证据与 PASS / PASS_WITH_LIMITS / FAIL 结论；Mock/Replay 不得包装成真实行情证据。

## Mac Tushare M0 可验证验收输入

### 全市场快照与字段

- [ ] 取得覆盖上海、深圳、北京的全 A 股批量快照，核对证券数量、代码、名称、前收、最新价、量、额、涨跌幅，以及停复牌/交易状态。
- [ ] 记录供应商原始字段到统一 domain 字段的映射、单位、空值和异常值处理；不把任何 Tushare 字段命名或展示为通达信紫黄线。

### 时间、去重与历史口径

- [ ] 每条事件保存 `source_ts`、`received_ts`、provider/config 版本和 Asia/Shanghai 时区；同一 `code + source_ts` 只处理一次。
- [ ] 验证至少三个交易日的日线与分钟线、复权口径、午休/集合竞价、交易日滚动和重复拉取差异。

### 板块与成分

- [ ] 验证行业/概念板块、成分、生效日、空集与开盘前刷新。
- [ ] 板块临时空结果保留最近成功快照并进入可解释降级，不得作为全量清空。

### 批量、性能与新鲜度

- [ ] 记录供应商限频、单次批量、全市场扫描与 TopN 深取耗时、CPU/内存、错误率、端到端 p50/p95 和新鲜度。
- [ ] 未获供应商正式承诺的 SLA 继续标记 `unknown`，不得从一次测试推导供应商保证。

### 时段与故障演练

- [ ] 连续运行至少 30 分钟，并另行覆盖一个完整交易时段。
- [ ] 演练开盘、午后、断网、token 失效、服务错误、重连、补数和重复数据，记录恢复时间、数据缺口及重复处理结果。
- [ ] 数据不新鲜进入 `STOPPED/RED` 并停止新候选；恢复必须先进入 `WARMING`，通过新鲜样本预热后才恢复提醒。

### 授权与结论

- [ ] 书面授权明确账号、套餐、价格、有效期，以及内部 2—3 人展示、历史保存、派生候选、日志/缓存、备份/删除和到期处置；技术可调用不等于获许可。
- [ ] 最终输出只能为 `PASS`、`PASS_WITH_LIMITS` 或 `FAIL`；每项限制必须绑定降级路线、owner 和下一触发点。
- [ ] Mock/Replay 证据只用于共享契约回归，不得冒充真实行情或 Windows/TdxQuant 证据。

## Windows TdxQuant M0 附加验收

- [ ] 完成 `docs/reference/v2.0/m0_checklist.md` 的 Windows/TdxQuant 适用项并附真实环境、软件/SDK 版本、账号授权和原始证据。
- [ ] 至少 3 只股票每 5 秒比对界面与程序值，连续 ≥30 分钟；紫黄线字段、单位、累计方式、历史与刷新能力在显示精度内一致率 ≥98%，否则明确 FAIL 或限制。
- [ ] 验证全市场基础快照、三日历史、板块、批量性能、开盘/午后、断网重连和一个完整交易时段，并记录 p50/p95、错误率与环境信息。
- [ ] Windows 结果独立给出 `PASS`、`PASS_WITH_LIMITS` 或 `FAIL`，每项限制绑定降级路线、owner 和下一触发点。

## 交付物

- 可重复运行且默认不回显秘密的路线级 `m0_probe`。
- 字段映射、provider/config 版本、目标环境与授权元数据。
- 路线级 `M0_report.md`，包含原始证据索引、性能/新鲜度结果、限制、降级路线、owner 和下一触发点。
- Provider 归一化、时间戳、去重、`STOPPED/RED` → `WARMING` 恢复与 Replay 回归结果。

FAIL 不使用网页抓取、OCR、鼠标脚本、未获支持的 HTTP 入口或替代资金字段绕过。

## 进度与依赖

| 日期 | 进展 | 状态 |
| --- | --- | --- |
| 2026-07-22 | 因当前只有 Mac，将真实 Windows/通达信 M0 从首版延后 | 计划中（环境待定） |
| 2026-07-23 | HAZ-406 完成 Mac 可移植性预检：共享核心回归、Provider readiness 降级契约与 [Windows 迁移清单](mac-portability-preflight.md) 已记录；Windows/TdxQuant 仍未验证 | PASS_WITH_LIMITS |
| 2026-07-23 | Human Owner 批准激活双路线真实数据闸门；HAZ-403 确认 Tushare Pro 仅为首个 Mac M0 候选，正式 HTTPS、授权和真实环境证据未齐 | `PASS_WITH_LIMITS`；真实接入 NO-GO |
| 2026-07-23 | HAZ-409 治理提交 `d45d3f1` 已以 fast-forward 合入本地 `main`，并从全新 detached worktree 回读 v0.3 激活与双路线验收口径 | 本地激活完成；GitHub 尚未同步 |
| 待启动 | HAZ-410 已登记为 backlog，承接 Mac Tushare 真实行情 M0；HAZ-409 的本地 `main` 依赖已满足，但 Mac 路线的正式 HTTPS、书面授权和受控真实环境前置条件仍未满足 | `backlog`；仅为未来 M0，不构成生产接入许可 |

## 决策与风险

| 决策/风险 | 理由/应对 |
| --- | --- |
| 本版本不阻塞 v0.1/v0.2 的本地工程与回放 | 可先降低技术不确定性，但不能宣称真实数据可用 |
| 双路线并行取证，不做平台二选一 | Mac/Tushare Pro 验证可得的真实行情与共享核心；Windows/TdxQuant 保留原规格、紫黄线和目标环境证据 |
| HAZ-403 只有 `PASS_WITH_LIMITS` | 官方材料仍存在明文 HTTP 入口；供应商不正式确认 HTTPS 则 Mac 路线 FAIL，真实 token 永不走 HTTP |
| 环境、账号和授权由 Human Owner 提供 | Agent 无法凭空证明硬件、账号、许可和真实界面一致性，也不得代为注册、购买或接受条款 |
| 资金模块保持 `unavailable` | Tushare 字段不冒充通达信紫黄线；等待独立资金字段 M0 |

## Session Handoff 索引

尚无。现场验证跨 session 时必须记录环境、样本、持续时间、错误与原始证据位置，但不得记录或回显 token。

## 封版记录

- 验证结果：待执行；当前只有 HAZ-403 `PASS_WITH_LIMITS` 的候选查证与 HAZ-406 Mac 可移植性预检。
- 遗留问题：任一路线 PASS/PASS_WITH_LIMITS 后仅承接该路线获证范围；FAIL 时必须重新规划，不自动推进。
- 终态对账：必需 M0、书面授权与真实环境证据均未完成；GitHub 尚未同步。
