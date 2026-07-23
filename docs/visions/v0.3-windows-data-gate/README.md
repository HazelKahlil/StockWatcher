# v0.3-windows-data-gate：Windows TdxQuant 真实数据闸门

> 状态：活跃（前置交付包已实现，等待 Windows 现场 M0）
> 创建：2026-07-22 ｜ 路线收敛：2026-07-23 ｜ 计划 tag：`v0.3.0`

## 权威结论

- Human Owner 已决定正式数据路线回到 Windows + 官方通达信 TdxQuant；当前为单人、只读测试，不购买或接入 Mac/Tushare/iFinD。
- HAZ-404 证明官方 TdxQuant 仍在维护，支持 `tqcenter` Python 调用和本机 `POST http://127.0.0.1:17709/`。本机 HTTP 不是供应商托管 HTTPS，不得开放到非回环地址。
- 官方免费 64 位“金融终端（量化模拟）”不含券商交易，适合作为现场 M0 起点；技术可调用不等于已获多人展示、保存或派生结果授权。
- Mac 只证明 Mock/Replay、归一化契约、离线测试和打包配置；不能证明 Windows、通达信、真实交易时段、紫黄线、性能或安装体验。
- 紫黄线、Level-2、`Zjl`、`Zjl_HB` 与公式口径在真实 M0 前全部保持 `unavailable`。

## HAZ-410 前置交付范围

- 官方 TdxQuant HTTP/Python 可替换传输层；Python 客户端延迟加载，Mac 无 TQ 时仍可运行 Mock/Replay、测试与 UI。
- 股票列表、批量价量、快照、历史行情、板块关系和交易日历的显式调用与归一化边界。
- 每条快照保留 `source_ts`、`received_ts`、provider/config 版本、交易状态和质量；官方响应缺精确 `source_ts` 时使用接收时间作显式 fallback，但健康只到 `WARMING`，候选保持关闭。
- 端口不可达、超时、未登录、接口/字段缺失、过期、重复时间戳、中断/恢复、非交易时段与用户暂停的可解释状态。
- Windows 单一 PowerShell 入口，覆盖安装/更新、预检、应用诊断界面、脱敏 M0 报告和分发包构建。
- PyInstaller、Inno Setup、版本信息、运行目录、卸载保留与离线检查。

## 开工与安全门

- [x] 只使用 HAZ-404 查证的一手官方能力；不使用 pytdx、逆向服务器、网页抓取、OCR 或鼠标脚本。
- [x] 代码不读取交易密码，不连接券商账户、持仓、订单或下单接口。
- [x] HTTP 端点只允许回环地址和官方端口 17709。
- [x] 资金字段保持 `unavailable`，未把 `Zjl`/`Zjl_HB` 或替代字段命名为紫黄线。
- [ ] Human Owner 在 Windows 安装并登录官方免费 64 位“金融终端（量化模拟）”。
- [ ] 现场确认终端/TdxQuant 版本、账号授权范围及本项目内部使用边界。
- [ ] 现场完成真实交易时段 M0，并形成 `PASS`、`PASS_WITH_LIMITS` 或 `FAIL`。

## Windows 现场验收

- [ ] 预检区分未安装、终端未启动/未登录、TQ 服务不可达、接口/字段不可用、非交易时段、数据中断和用户暂停。
- [ ] 全 A 股列表和批量价量覆盖沪深京；记录数量、字段、耗时、限频、错误率和 p50/p95。
- [ ] 至少 3 只股票每 5 秒比对界面与程序值，连续不少于 30 分钟。
- [ ] 验证三日分钟历史、行业/概念及成分、交易日历、开盘/午后、断网重连、补数和完整交易时段。
- [ ] 验证精确供应商源时间；若官方接口仍不提供，必须保持限制并不得把 `received_ts` 冒充 `source_ts`。
- [ ] 紫黄线的字段、颜色、公式、单位、累计、刷新、历史和权限一致率达到验收要求；否则资金模块继续关闭。
- [ ] Windows 构建、安装、启动、日志/数据库目录、卸载和回滚实际可用。
- [ ] M0 报告逐项区分已验证与未验证，不把 Mac/CI 结果外推。

完整现场清单还须回读 `docs/reference/v2.0/m0_checklist.md`。

## 入口与交付物

- Human Owner 操作：[Windows 一页交接](windows-handoff.md)。
- PowerShell：`scripts/windows/stockwatcher.ps1`。
- JSON/Markdown 探针：`python -m stock_watcher.providers.tdxquant_m0 --output <目录>`。
- 报告口径：[M0 报告模板](m0-report-template.md)。
- 打包：`packaging/stockwatcher.spec`、`packaging/windows/StockWatcher.iss`。

## 进度

| 日期 | 进展 | 状态 |
| --- | --- | --- |
| 2026-07-23 | HAZ-406 完成 Mac 可移植性预检；只证明共享核心在无 TQ 环境下安全降级 | `PASS_WITH_LIMITS` |
| 2026-07-23 | HAZ-404 核验官方 TdxQuant、免费量化模拟终端与 127.0.0.1:17709 路线 | `PASS_WITH_LIMITS`；实施仍需 M0 |
| 2026-07-23 | HAZ-409 将 v0.3 激活到本地 `main`；GitHub 尚未同步 | 本地激活完成 |
| 2026-07-23 | Human Owner 收敛路线：停止 Mac/Tushare/iFinD 执行，改为 Windows/TdxQuant 单人只读测试 | 当前权威路线 |
| 2026-07-23 | HAZ-410 完成 Provider/传输/预检/M0/PowerShell/打包前置与离线回归 | 实现完成，待 Windows 现场 |
| 2026-07-23 | HAZ-418 修复板块/日历归一化、STALE 恢复门与 PowerShell 失败语义；新增 Python 3.11/3.12 `windows-latest` 无终端构建矩阵 | 候选分支验证中；不等于真实 TdxQuant M0 |

## 验证边界

- 当前可验证：Mac + Python 3.12 下的 Mock/Replay、TdxQuant fixture 契约、失败/恢复、安全门、PyInstaller 配置与 Windows 包离线检查。
- 当前未验证：Windows、真实 TdxQuant、真实行情、终端登录、精确源时间、紫黄线、Level-2、完整交易时段、Windows 通知、多屏、安装器与卸载体验。
- GitHub：本地 `main` 比 `origin/main` 领先；HAZ-418 仅为取得真实 `windows-latest` 证据推送候选分支并使用单一 draft 里程碑 PR，不合入本地 `main`。

HAZ-410 在 macOS + Python 3.12 的前置验证：`uv sync --all-groups --frozen`、`uv lock --check`、62 项 pytest、Ruff、Mypy、workspace validation、Windows package offline contract、`git diff --check` 均通过；离屏 Replay smoke 生成 5 张状态图；PyInstaller 成功生成当前 macOS 架构目录且冻结程序 `--help` 可启动。这些结果不构成 Windows 构建或 TdxQuant 真机证据。

## Session Handoff

下一位现场执行者先读本文件和 [Windows 一页交接](windows-handoff.md)，再在真实 Windows 运行 PowerShell 的“预检”和“M0 探针”。失败不得改接非官方数据源；按报告中的具体原因修复或给出 `FAIL`。
