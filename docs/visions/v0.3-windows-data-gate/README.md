# v0.3-windows-data-gate：Windows TdxQuant 真实数据闸门

> 状态：活跃（HAZ-526 后继便携候选的 Mac 工程门已通过；HAZ-527 在候选下载前被 Windows runner 阻断，仍待 Human Owner 的 Windows 普通用户 live readback，再进入现场 TdxQuant M0）
> 创建：2026-07-22 ｜ 路线收敛：2026-07-23 ｜ 计划 tag：`v0.3.0`

## 权威结论

- Human Owner 已决定正式数据路线回到 Windows + 官方通达信 TdxQuant；当前为单人、只读测试，不购买或接入 Mac/Tushare/iFinD。
- HAZ-404 证明官方 TdxQuant 仍在维护，支持 `tqcenter` Python 调用和本机 `POST http://127.0.0.1:17709/`。本机 HTTP 不是供应商托管 HTTPS，不得开放到非回环地址。
- 官方免费 64 位“金融终端（量化模拟）”不含券商交易，适合作为现场 M0 起点；技术可调用不等于已获多人展示、保存或派生结果授权。
- Mac 只证明 Mock/Replay、归一化契约、离线测试和打包配置；不能证明 Windows、通达信、真实交易时段、紫黄线、性能或安装体验。
- 独立真实 Windows v9 验证只证明冻结候选的 Python 3.11/3.12 工程与打包链；没有连接 TdxQuant 终端或行情，不能替代现场 M0。
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
- 跨环境执行：[Windows Codex 直接交接](session-handoff-windows-codex.md)。
- 内部便携双击入口：`packaging/windows/portable/启动 StockWatcher.vbs`；冻结 ZIP 由 `scripts/build_internal_portable.py` 生成。
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
| 2026-07-24 | HAZ-420 为冻结候选补齐 Windows `tzdata`、PowerShell 5.1 UTF-8 BOM 字节门禁，并将 macOS/Windows 无 TQ 服务预检测试改为显式宿主契约；Mac 全量门禁与离屏 Replay smoke 通过 | 源码候选已修复；仍待真实 Windows Builder 重打包与双版本矩阵复验 |
| 2026-07-24 | HAZ-423 修复 PowerShell Setup 的内嵌 Python 版本判断语法，并以实际 `-c` 片段的 AST 回归锁定 3.11–3.12 支持范围 | 新冻结候选待 Windows Builder 重打包与双版本矩阵复验 |
| 2026-07-24 | HAZ-430 对冻结候选 `7d5c8b07dd714d4f209528d23074692e8644103c` 完成独立真实 Windows v9 只读验收：23/23 步骤通过，Python 3.11/3.12 各 74 项测试，PowerShell、PyInstaller 与 Inno Setup 通过 | `PASS`；仅证明无终端工程/打包链 |
| 2026-07-24 | HAZ-417 对 HAZ-415 三项 blocker 的唯一复审为 `PASS`；HAZ-416 将精确候选 fast-forward 合入本地 `main`，Mac 最终门禁 74 项测试与五状态 Replay smoke 通过，并复用单一 draft PR #2 做私有 GitHub 里程碑同步 | 本地候选收口；PR 待 Human Owner 审核/合入，真实 TdxQuant M0 待现场 |
| 2026-07-24 | HAZ-439 修复真实 Windows 暴露的 Preflight 失败报告缺失与供应商 detail 泄漏风险：API 会话失败统一使用稳定 reason 和固定脱敏消息，安全兜底异常先形成 `FAIL` 报告再非零退出；Windows 矩阵增加失败报告回读及中文/空格路径合约 | Mac fixture/offline 门禁通过；新冻结候选仍待独立真实 Windows + 官方 TdxQuant 复验 |
| 2026-07-24 | HAZ-443 收紧 Preflight 成功结构与报告终态不变量：畸形/空/非法股票列表、缺失或重复 `api_session` 均不得形成整体 `PASS` 或 `windows_live_verified=true` | Mac fixture/offline 回归；仍不构成真实 Windows/TdxQuant 证据 |
| 2026-07-24 | HAZ-447 针对真实 Windows 的 267 字符 ISCC 输入与原生 Preflight 缺报告 blocker：Build 改用临时短盘符和独占 staging，成功后再发布 installer/portable ZIP；Preflight 对启动失败、非零、缺失/畸形报告先落盘固定脱敏 `FAIL` 再返回非零 | Mac 深路径/中文空格 fixture 与离线契约通过；真实 ISCC、PowerShell、App Control 和 TdxQuant 仍待 Windows 复验 |
| 2026-07-24 | HAZ-449 闭合独立复核发现的发布与 Preflight 语义缺口：全部发布路径继续使用短映射，双产物以备份/提交/回滚事务发布；Preflight 固定检查集合并重算聚合终态，子进程终态矛盾强制替换为固定脱敏 `FAIL` | macOS 上以真实 PowerShell 7.5.2 执行深路径、中文空格、重复发布、第二产物故障回滚及 Preflight 异常矩阵；仍待真实 Windows PowerShell 5.1/ISCC/App Control/TdxQuant 复验 |
| 2026-07-24 | HAZ-452 修复 Preflight 成功语义：仅固定、完整、无重复的 canonical checks 全部 `PASS` 且 `windows_live_verified=true` 才能零退出；不完整/fallback/单项成功、未知或重复检查、顶层聚合矛盾及 live 双向矛盾均 fail-closed | macOS Python/离线门禁通过；本机无 PowerShell，行为用例如实跳过，仍待独立真实 Windows PowerShell 7.x 零跳过复核与最终 Gate |
| 2026-07-27 | HAZ-511 基于真实官方 TQ `ErrorId=10 → 0` 证据，在 Preflight 与运行期 Provider 的 `get_stock_list` 请求中固定补齐整数 `list_type: 0`；回归覆盖旧参数失败、新参数成功、两条调用路径及供应商非零错误 fail-closed | 后继源码候选完成 macOS 离线回归；仍待 Authenticode 签名、目标 Windows 安装/启动与独立原生 Preflight，不等于客户交付或交易时段 M0 通过 |
| 2026-07-27 | HAZ-512 基于 HAZ-511 候选增加内部单机离线便携入口：验签官方 Python 3.12/Pythonw 后无控制台启动，TQ 不可达时只尝试启动验签官方终端，固定最小只读参数检查，提供中文重试、单实例和可选普通用户桌面快捷方式 | macOS 标准库逻辑、静态安全契约及中文/空格隔离打包回归；目标 Windows VBS/Pythonw、Application Control、终端启动和真实 TQ 仍待独立验证 |
| 2026-07-27 | HAZ-515 修复 HAZ-512 便携 ZIP 只有探测外壳的 blocker：包内纳入完整应用树、依赖声明、PySide6 UI 与原生 Preflight；启动前只读检查依赖，且仅在整体 `PASS`、恰好一个 `api_session=PASS`、`windows_live_verified=true` 时进入真实 TdxQuant UI | Mac 离线机械合同与全新目录解包/import smoke；仍待 HAZ-497 在同一冻结 ZIP 上复验目标 Windows Pythonw、依赖、原生报告、UI 与 Application Control |
| 2026-07-27 | HAZ-526 移除原生 Preflight 失败后自动启动 `TdxW.exe` 的路径；终端未运行、TQ 未就绪或 Preflight 未通过时只中文 fail-closed，并以机械合同锁定 VBS/Python 链无 elevation verb | Mac 源码、单测和离线合同候选；不代表真实 Windows/UAC 已通过，须对新冻结 ZIP 开独立普通用户复验 |
| 2026-07-27 | HAZ-527 三轮均在 Explorer 枚举前被 Windows Computer Use `runner pipe-in` 阻断；冻结附件未下载、未解压，候选、Preflight/API/TQ、UI、UAC 与 Windows 设置均未触碰 | 平台/运行时 blocker；对 HAZ-526 候选没有形成 PASS 或 FAIL，真实 Windows live readback 与 M0 继续未完成 |
| 2026-07-27 | HAZ-536 将 `6e5dbed8eee027ef7d5478b18b1539b3c16a24ed` 作为 HAZ-511/512/515/526 的唯一线性后继，复核冻结 ZIP/manifest/payload，并建立脱离 Multica 后可直接交给 Windows Codex 的 handoff | GitHub 交接点；Mac 工程证据与 Windows 未验证门严格分开 |

## 验证边界

- 当前可验证：Mac + Python 3.12 下的 Mock/Replay、TdxQuant fixture 契约、失败/恢复和安全门；独立真实 Windows 下 Python 3.11/3.12 的安装、导入、CLI、74 项测试、Ruff、Mypy、PowerShell 失败闭环、PyInstaller 与 Inno Setup。
- 当前未验证：真实 TdxQuant、真实行情、终端登录、精确源时间、紫黄线、Level-2、完整交易时段、Windows 通知、多屏，以及 Human Owner 机器上的安装与卸载体验。
- HAZ-515 完整便携候选在 Mac 仅验证应用树/原生 Preflight/UI 入包、启动门、依赖 fail-closed、全新目录 import、manifest 和中文/空格路径合同；VBS、官方 Pythonw、预置依赖、Authenticode、Application Control、原生报告与真实 UI 必须由 HAZ-497 按同一冻结 ZIP 在目标 Windows 复验。
- HAZ-526 后继候选在 Mac 仅验证 Preflight 失败不会自动启动官方终端、VBS/Python 启动链无 elevation verb，以及新 ZIP 的离线完整性；UAC 请求者与普通用户真实启动结果仍须在全新 Windows 轮次确认。HAZ-527 没有运行到候选下载，不得当作候选失败或通过。
- GitHub：本地 `main` 仍停在较早候选 `6e193a3c20177220d89a7497004af281a7509270`；HAZ-511/512/515/526 形成其 9 个提交的线性后继 `6e5dbed8eee027ef7d5478b18b1539b3c16a24ed`。为避免重复分支/PR，交接复用 HAZ-418 已建立的 `fix/HAZ-418-blockers` 远端 head 与单一 draft PR #2；`origin/main` 在 PR 合入前仍未同步。

GitHub Actions run `30062601762` 对候选 commit 的三个 jobs 均在 `steps=[]`、`runner_id=0` 时因账户 payment/spending limit 失败，不能表述为 CI PASS。Human Owner 已明确接受 HAZ-430 的独立真实 Windows v9 只读 `PASS` 作为该平台阻塞的替代工程证据；不得等待、规避或更改 billing。

HAZ-410 在 macOS + Python 3.12 的前置验证：`uv sync --all-groups --frozen`、`uv lock --check`、62 项 pytest、Ruff、Mypy、workspace validation、Windows package offline contract、`git diff --check` 均通过；离屏 Replay smoke 生成 5 张状态图；PyInstaller 成功生成当前 macOS 架构目录且冻结程序 `--help` 可启动。这些结果不构成 Windows 构建或 TdxQuant 真机证据。

HAZ-416 在最终文档树上的 macOS + Python 3.12 回归：`uv sync --all-groups --frozen`、`uv lock --check`、Provider/恢复/Windows 包定向 39 项与全量 74 项 pytest、Ruff、Mypy（37 个源文件）、Windows 包离线契约、workspace validation（27 个必需文件）、离屏 Replay 五状态 smoke 与 `git diff --check` 均通过。真实 Windows 工程/打包证据仍以 HAZ-430 对代码候选 `7d5c8b07dd714d4f209528d23074692e8644103c` 的独立结果为准。

HAZ-439 在 macOS + Python 3.12 的修复回归：`uv sync --all-groups --frozen`、`uv lock --check`、Preflight/Windows 包定向 36 项与全量 82 项 pytest、Ruff、Mypy（37 个源文件）、Windows 包离线契约、workspace validation（27 个必需文件）、离屏 Replay 五状态 smoke 与 `git diff --check` 均通过。测试以 fixture/monkeypatch 覆盖供应商非零错误、意外响应、兜底异常、进程控制信号、失败报告落盘/脱敏和中文/空格路径；这些 Mac/静态结果不构成真实 Windows PowerShell 或 TdxQuant 现场证据。

HAZ-443 在 macOS + Python 3.12 的返修回归：`uv sync --all-groups --frozen`、`uv lock --check`、Preflight/Windows 包定向 46 项与全量 92 项 pytest、Ruff、Mypy（37 个源文件）、Windows 包离线契约、workspace validation（27 个必需文件）、离屏 Replay 五状态 smoke 与 `git diff --check` 均通过。新增 fixture/monkeypatch 回归证明畸形/空/非法股票列表及缺失、重复、非 PASS 的 `api_session` 不能形成整体 `PASS` 或 Windows live 验证；这些 Mac/静态结果仍不构成真实 Windows PowerShell 或 TdxQuant 现场证据。

HAZ-447 在 macOS + Python 3.12 的修复回归：`uv sync --all-groups --frozen`、`uv lock --check`、Build/Preflight/Windows 包定向 51 项与全量 97 项 pytest、Ruff、Mypy、Windows 包离线契约、workspace validation、离屏 Replay 五状态 smoke 与 `git diff --check` 均通过。深层 checkout、中文/空格路径、240 字符 ISCC 保守预算、重复/失败清理、子进程非零/启动失败及缺失/畸形报告均由 fixture/静态契约覆盖；这些 Mac 结果不能替代真实 Windows ISCC、PowerShell、App Control 或 TdxQuant 复验。

HAZ-449 在 macOS + Python 3.12 与 PowerShell 7.5.2 的返修回归：Build/Preflight/Windows 包定向 63 项与全量 109 项 pytest、Ruff、Mypy（37 个源文件）、Windows 包离线契约、workspace validation（27 个必需文件）、Replay 五状态 smoke 与 `git diff --check` 均通过。真实 PowerShell 行为测试覆盖子进程 0/非 0、启动失败、报告缺失、非法 UTF-8/JSON、schema/检查集合/聚合矛盾、中文空格参数与报告目录，以及合法 PASS/FAIL；实际文件系统测试覆盖超过 260 字符的中文空格深树、成功与重复发布、第二产物发布故障回滚和事务 staging 清理。Mac 结果不构成真实 Windows PowerShell 5.1、ISCC、App Control 或 TdxQuant 现场证据。

HAZ-452 在 macOS + Python 3.12 的返修回归：`uv sync --all-groups --frozen`、`uv lock --check`、定向 Preflight/Windows 包测试、全量 105 项通过/20 项 PowerShell 宿主跳过的 pytest、Ruff、Mypy（37 个源文件）、Windows 包离线契约、workspace validation（27 个必需文件）、Replay 五状态 smoke 与 `git diff --check` 均通过。新增直接反例覆盖不完整/fallback/仅 `api_session=PASS`、重复/未知/缺失检查、顶层聚合矛盾、完整 PASS + `live=false`、非 PASS + `live=true` 及子进程终态矛盾；本机未发现可调用的 `pwsh`，因此不能复用 HAZ-449 的 PowerShell 7.5.2 证据，也不构成真实 Windows、TdxQuant、行情或 M0 证据。

HAZ-511 在 macOS + Python 3.12.11 的参数修复回归：`uv sync --all-groups --frozen`、`uv lock --check`、全量 pytest（109 passed、20 skipped；跳过项均因本机无 `pwsh`）、Ruff、Mypy（37 个源文件）、Windows 包离线契约、workspace validation（27 个必需文件）、Replay 五状态 smoke、PyInstaller 当前 macOS 架构构建与冻结程序 `--help`、`git diff --check` 均通过。模拟当前官方桥时，旧 `{"market":"5"}` 请求被供应商非零错误拒绝，显式整数 `{"market":"5","list_type":0}` 成功；Preflight 与运行期 Provider 均固定传递新参数，供应商 `ErrorId != 0` 继续形成整体 `FAIL` 且 `windows_live_verified=false`。本轮结果仅为 Mac 离线契约与源码候选证据；未执行真实 Windows PowerShell 5.1/7.x、PyInstaller/Inno Setup、Authenticode 签名、目标 Windows 安装/启动、真实官方 TQ 原生 Preflight、交易时段 M0 或客户交付验证。

HAZ-515 在 macOS + Python 3.12.11 的完整便携修复回归：冻结依赖检查、全量 pytest（117 passed、20 skipped；跳过项均为当前宿主缺少 `pwsh`）、Ruff、Mypy（41 个源文件）、Windows 包离线合同、workspace validation（27 个必需文件）、Replay 五状态 smoke、`git diff --check` 与全新中文/空格目录解包/import/入口解析均通过。ZIP 机械检查覆盖完整应用树、原生 Preflight、真实 UI 入口、缺应用/缺 Preflight/缺依赖、严格 PASS 门、供应商非零/畸形结果和全量 payload manifest；这些 macOS/静态证据仍不构成目标 Windows Pythonw、Application Control、官方 TdxQuant 或真实 UI 验证。

HAZ-526 在 macOS 27.0 arm64 + Python 3.12.11 的启动链修复回归：`uv sync --all-groups`、全量 pytest（118 passed、20 skipped；跳过项均为当前宿主缺少 `pwsh`）、Ruff、Mypy（38 个源文件）、Windows 包离线合同、workspace validation（27 个必需文件）、`uv lock --check`、`git diff --check` 与 `QT_QPA_PLATFORM=offscreen uv run python scripts/capture_mac_ui_evidence.py <全新临时目录>` 五状态 Replay smoke 均通过。新增回归锁定 Preflight 失败不启动终端、VBS 无 elevation verb；这些 Mac/静态结果不证明 Windows 普通用户启动或 UAC 已通过。

## Session Handoff

Human Owner 到 Windows 后先读 [Windows Codex 直接交接](session-handoff-windows-codex.md)；其中包含冻结输入、精确 GitHub ref、HAZ-527 边界和可直接复制的完整提示词。[Windows 一页交接](windows-handoff.md)继续作为操作速查。失败不得改接非官方数据源；按首个真实失败点保存证据并停止。
