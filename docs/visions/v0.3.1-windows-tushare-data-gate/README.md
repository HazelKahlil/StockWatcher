# v0.3.1-windows-tushare-data-gate：跨平台 Tushare 数据闸门

> 状态：进行中  
> 创建：2026-07-29  
> 当前执行平台：Windows  
> 目标分支：`feat/windows-tushare-data-gate`

## 决策

Human Owner 于 2026-07-29 明确批准将默认主数据路线从
“Windows + 官方 TdxQuant”调整为“跨平台 Tushare 兼容 HTTP 数据源”：

- 超级接口为默认主接口；
- 快速接口只在真实 M0 证明更快、字段一致且稳定后作为允许列表内的可选加速；
- TdxQuant 保留为可选诊断和未来资金字段探索，不再是应用正常启动或候选放行的必要前提；
- Windows 先完成；Mac 从同一 commit 复用 Provider、模型、归一化、算法、SQLite 和核心 UI；
- 紫黄资金线继续保持独立 `unavailable`，不得用 moneyflow、Level-1 或盘口字段冒充。
- Human Owner 于 2026-07-29 15:00 后明确授权供应商文档中的 Tushare SDK
  `realtime_quote(src="sina")` 原生实时路线。它作为独立实验 profile，不改写 13:00
  `rt_k` 首次 M0 `FAIL`，也不在交易时段 M0 前放行候选。

本决策是 `docs/reference/v2.0/requirements.lock.json` 的后继产品路线批准，不静默改写原始
锁定文件。股票池、固定三只、强/中/近、提醒频率、停止门和禁止交易等锁定规则不变。

## 里程碑 A：Tushare Data Gate

- [x] Super/Fast 地址和 profile 可配置，业务层不硬编码完整接口路径。
- [x] 正式凭据默认保存在系统安全存储；配置、日志、SQLite、Git 和包内无 secret。
- [x] 设置页支持隐藏输入、测试、原子替换、清除和明确的中文错误分类。
- [x] 统一响应解析支持 fields/items、list/dict、空数据、供应商 code 和 HTTP 错误。
- [x] Provider 输出归一化模型，不向业务层暴露供应商原始字典。
- [ ] 全 A 列表、交易日历、日线、最近三日 1/5 分钟和板块已通过；可信实时源时间未通过。
- [ ] 全市场实时数据连续至少 30 分钟，记录 p50/p95、错误率、429/503、停滞和恢复。
- [ ] 方案一/二在相同条件下完成性能、一致性和权限比较。
- [ ] Level-2 只形成实验结论，不进入评分。
- [ ] M0 结论只写 `PASS`、`PASS_WITH_LIMITS` 或 `FAIL`。

## 里程碑 B：Windows 真实候选闭环

只有里程碑 A 为 `PASS` 或足够明确的 `PASS_WITH_LIMITS` 才开工：

- 应用正常启动不依赖通达信；
- 全市场 5—10 秒基础扫描，TopN 分钟和板块深取；
- 本地计算 1/3/5 分钟涨速、量额变化、三日趋势和板块共振；
- 数据健康且预热完成后固定输出真实三只，标记强/中/近；
- 09:45、14:50 和盘中特别强异动提醒形成 Windows 真实闭环；
- 数据失败或切源时关闭候选门、清空基线，至少三周期新鲜预热后恢复；
- 完成 30 分钟交易时段与一个完整交易日验证；
- 普通用户、无 UAC、无控制台的便携版或安装版可重复运行。

## 安全与凭据

- Human Owner 对本次现场凭据保存给出了明确授权；凭据值不得进入文档、报告或提交。
- 正式凭据优先存入 Windows Credential Manager；未来 Mac 使用 Keychain。
- 环境变量只允许显式开发/测试：`STOCKWATCHER_TUSHARE_SUPER_API_KEY` 与
  `STOCKWATCHER_TUSHARE_FAST_TOKEN`。
- secret 不进入源码、YAML、JSON、SQLite、README、fixture、Git、日志、截图、命令行或报告。
- TLS 验证保持开启；系统代理行为可配置，默认不使用环境代理。

## M0 阶段

1. 凭据与能力：health、status、catalog、小范围交易日历、股票基础和 Fast 小请求。
2. 静态/历史：全 A、市场分布、ST/退市字段、日线、三日 1/5 分钟、行业/概念/成分。
3. 全市场实时：至少 30 分钟，验证真实更新时间、10 秒预算、错误率、停滞和恢复。
4. 实时分钟：闭合 K、延迟、批量上限、频率和 TopN 规模。
5. 板块：申万/TDX/概念、成员稳定性和抽样一致性。
6. Level-2：仅实验，不进入候选或紫黄线。
7. Super/Fast 同条件比较。

真实测试使用显式 `live_tushare` 标记；默认 pytest 不依赖 Key 或外网。

## 当前进度

| 日期 | 进展 | 状态 |
|---|---|---|
| 2026-07-29 | 保存 `2ea85fa` 完整本地 bundle；从该 commit 创建执行分支 | 完成 |
| 2026-07-29 | 记录主数据路线、跨平台和 fail-closed 决策 | 完成 |
| 2026-07-29 | CredentialStore、设置 UI、HTTP 传输与离线测试 | 完成 |
| 2026-07-29 | Super/Fast 能力闸门 | 两者 `PASS_WITH_LIMITS` |
| 2026-07-29 | 5,530 条在市证券、日线、交易日历、三样本三日 1/5 分钟、行业/概念/成分 | 通过 |
| 2026-07-29 | 13:00:02 全市场实时 30 分钟首次尝试 | `FAIL`：5 轮均 `empty_data`，错误率 100%，运行按门禁停止 |
| 2026-07-29 | 单只 `rt_k` 与三只 `rt_min` 诊断 | `rt_k` 空数据；`rt_min` HTTP 400 业务错误 |
| 2026-07-29 | 按官方合同补齐 `freq=1MIN` 后复核 Super/Fast 实时能力 | Super `rt_k` 单股/全市场 HTTP 200 空数据，`rt_min`/`rt_min_daily` 超时；Fast `rt_k` 业务错误 |
| 2026-07-29 | Windows 图标、启动即自动检测、人工实时检测与明确问题位置 | 源码 UI smoke 通过；实时门继续 fail-closed |
| 2026-07-29 | PyInstaller 6.21.0 + Inno Setup 6.7.3 构建 0.3.1-alpha 安装器与 ZIP | 构建通过；本机 Application Control 阻止未签名冻结 EXE，未绕过策略 |
| 2026-07-29 | 盘后按明确授权验证 `tushare.realtime_quote(src="sina")` | 单批上限 800；全 A 5,530/5,530、0 重复、7 批总耗时 7.781 秒、每行均有 `DATE/TIME`；仅属非权威盘后工程证据 |

## 当前结论

Tushare 静态、日线、三日历史分钟和板块能力真实可用，但盘中实时能力未通过。
本次 M0 结论为 `FAIL`；首个严格失败报告必须保留。供应商开通或修复 `rt_k`/`rt_min`
实时权限并返回带可信源时间的数据前，真实候选、09:45/14:50 和盘中特别强异动提醒
保持关闭。紫黄线、Level-2 继续为 `unavailable`。

界面现在会在启动后立即自动检测并每 60 秒复核，分开展示基础接口连接、实时数据
状态、候选门和具体问题；人工“立即检测实时数据”使用同一只读、脱敏路径。它不把
连接成功冒充为实时 M0 成功。

新增 `native_realtime` 路线已经证明盘后全市场吞吐具备 10 秒预算，但旧日期证券和盘后
时间语义仍需严格过滤。下一权威交易时段必须连续至少 30 分钟验证逐行新鲜度、价量推进、
错误率、p50/p95、断线恢复和三周期预热；通过前本版总 M0 结论仍是 `FAIL`。
