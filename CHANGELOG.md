# Changelog

本项目采用 [Semantic Versioning](https://semver.org/)；所有值得用户或维护者关注的变化记录在这里。

## [Unreleased]

### Changed

- Tushare 主界面启动后立即、随后每 60 秒自动区分基础连接与实时快照状态；
  人工按钮改为“立即检测实时数据”；经授权的原生实时路线使用本机 Fast 凭据
  执行单证券脱敏探测，空数据、超时、权限、频控和缺供应商时间均给出明确问题
  位置，候选继续 fail-closed。
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

- 经 Human Owner 明确授权的 Tushare SDK 原生实时 Provider：固定供应商验证入口、
  800 只批次上限、0.5 秒最小请求间隔、供应商日期/时间来源校验、脱敏全市场 M0
  工具和冻结运行时的最小 SDK 模块收集。2026-07-29 盘后工程探测覆盖 5530/5530，只作为
  `NON_AUTHORITATIVE_ENGINEERING_CHECK`；交易时段 M0、单位核对与候选开放仍待验证。
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
