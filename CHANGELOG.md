# Changelog

本项目采用 [Semantic Versioning](https://semver.org/)；所有值得用户或维护者关注的变化记录在这里。

## [Unreleased]

### Changed

- 开发方式调整为 Mac 本地优先，GitHub 仅在版本节点或明确备份/交接需求时同步。
- 版本路线重排：先完成 Mac Replay 基础和本地 Alpha，再进入 Windows/通达信真实数据闸门。
- v0.3 执行路线收敛为 Windows + 官方 TdxQuant 单人只读测试；Mac 仅保留 Mock/Replay 与离线契约验证，不购买或接入 Tushare/iFinD。

### Added

- v0.1 Mac Replay Foundation：跨平台 Python 工程、Provider 协议、Mock/Replay/Synthetic、SQLite WAL/配置与脱敏滚动日志基础，以及确定性测试。
- STOPPED 恢复来源时间门：拒绝并计数不晚于 STOPPED 截止线的延迟样本，防止旧数据重新放行候选。
- v0.2 Mac Local Alpha：固定三只候选、确定性回放提醒与可追溯 SQLite 记录，以及基于 Mock/Replay 的 Mac 本地界面（当前观察、三只提醒、数据中断、详情和历史）。
- v0.3 TdxQuant 前置交付包：官方本机 HTTP/可选 Python 传输、行情归一化、健康与恢复门、Windows 一键入口、脱敏 M0 报告和 PyInstaller/Inno Setup 配置。

### Fixed

- v0.3 板块关系与交易日历改为实际返回带时间、版本和质量元数据的统一 domain 对象；陈旧/中断后的行情必须完成配置数量的恢复预热样本后才重新放行。
- Windows 入口仅选择项目支持的 Python 3.11/3.12，原生命令失败立即返回非零；独立真实 Windows 无终端验收在两个版本上验证安装、导入、CLI、PowerShell、PyInstaller 和 Inno Setup 链路。GitHub Actions 因账户 billing 未启动，不能表述为 CI PASS。
- TdxQuant Preflight 在 API 会话返回供应商错误、意外响应或安全兜底异常时，先写出结构完整的 UTF-8 脱敏 `FAIL` 报告，再保持 PowerShell 非零退出；报告不再包含供应商原始 detail。
- Windows Build 通过临时短盘符把 ISCC、临时文件、备份、替换和最终产物路径全部约束在经典路径预算内，并以可回滚事务成组发布 installer/portable ZIP；Preflight 严格校验 UTF-8、固定 schema/检查集合及重算聚合终态，对子进程启动失败、非零退出、缺失或语义畸形报告统一先原子落盘固定、脱敏的 `FAIL` 报告，再传播失败语义。

### Validation

- 冻结代码候选 `7d5c8b07dd714d4f209528d23074692e8644103c` 已通过独立真实 Windows 无终端工程/打包验证：Python 3.11/3.12 各 74 项测试、PowerShell Setup 与 loopback Preflight 失败闭环、PyInstaller 和 Inno Setup 均通过。该结论不包含真实 TdxQuant、行情、紫黄线、交易时段、通知、多屏或安装卸载体验。

### Pending synchronization

- v0.1/v0.2 与 v0.3 前置交付候选已合入本地 `main`，并由单一 draft PR #2 镜像到私有 GitHub；PR 尚未合入 `origin/main`，不得称为远端已发布。
- v0.3 已有 Mac 全量回归和独立真实 Windows 无终端工程/打包证据；真实 TdxQuant、行情、紫黄线、完整交易时段、通知/多屏和安装卸载体验仍待 Human Owner 现场 M0。

## [0.0.0] - 2026-07-22

### Added

- 建立 StockWatcher 私有 GitHub 项目与本地权威目录。
- 安装 Kahlil Project Workflow 文档骨架、领域规则、版本路线与发布治理。
- 导入 V2.0 机器可读规格、锁定项、M0 清单、验收清单、配置/Schema 样例和配图。
- 增加 Bootstrap 文档完整性检查与 GitHub Actions 治理检查。
