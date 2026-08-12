# 版本索引

> 最后更新：2026-08-12
> 一个工作版本一个目录，内含 `README.md`。产品版本与 V2.0 规格文档版本不要混淆。

## 活跃版本

- **[v0.6-candidate-outcomes](v0.6-candidate-outcomes/README.md)** —
  正式 09:45/14:45 三只候选的下一真实交易日同档理论复盘、SQLite v8、历史回补与桌面 UI
  （状态：本地实现与离线工程验证已通过 PR #6 同步 GitHub；真实同点行情仍待交易日现场验收）。
- **[v0.4.0-alpha.2-internal-baseline](v0.4.0-alpha.2-internal-baseline/README.md)** —
  Mac / Web / Windows 当前状态的可重建内部试用基准（状态：源码基准；非商业发布、非权威
  M0；Web 继续 `BLOCKED / NOT_ACCEPTED`）。
- **[v0.4.1-shared-connection-gate](v0.4.1-shared-connection-gate/README.md)** —
  跨平台 Tushare Token 连接门、能力检测与应用级限流返修（状态：本地完成，已由 Mac 分支消费）。
- **[v0.4.2-macos-v1-port](v0.4.2-macos-v1-port/README.md)** —
  Mac V1 真实数据、系统钥匙串、生命周期、通知、恢复与内部 `.app` 验证
  （状态：Mac V1 内部试用主线；新鲜固定时点、15:30 准点和 Windows 真实验收仍未完成）。
- **[v0.4-v1-feature-complete](v0.4-v1-feature-complete/README.md)** —
  Windows 全市场真实 Top3、稳定替换、提醒、历史与总结闭环（状态：Windows 真实验收未通过）。
- [v0.3.1-windows-tushare-data-gate](v0.3.1-windows-tushare-data-gate/README.md) —
  历史数据接入阶段；其 Super/Fast 默认路线已被 2026-07-29 V1 基线取代。
- [v0.3-windows-data-gate](v0.3-windows-data-gate/README.md) — 已取得 Windows/TdxQuant
  live readback 与部分真实数据证据；分钟历史和权威 M0 未通过。TdxQuant 转为可选诊断路线。

## 同步状态

- 日常权威源：`/Users/kahlilhazel/Documents/700-AI-Workspace/20-Projects/StockWatcher` 中的本地 `main`。
- `v0.4.0-alpha.2` 通过里程碑发布分支同步；只有发布 PR 合入、local/remote `main` 对齐并
  创建 annotated tag 后，tag 才是本基准的最终源码入口。
- Web 的 `bf447ba` 是独立轨道固定点，不因 Shared Core tag 创建而成为 main 的一部分。

## 版本列表

| 版本 | 目标 | 状态 | 封版日期 |
| --- | --- | --- | --- |
| [v0.0-project-bootstrap](v0.0-project-bootstrap/README.md) | 建立仓库、治理骨架、版本路线并迁入 V2.0 交接基线 | 已封版 | 2026-07-22 |
| [v0.1-mac-replay-foundation](v0.1-mac-replay-foundation/README.md) | 在 Mac 建立可复现的跨平台工程与 Replay 基础 | 本地完成，draft PR #2 待合入 | 2026-07-23 |
| [v0.2-mac-local-alpha](v0.2-mac-local-alpha/README.md) | 交付基于 Mock/Replay 的 Mac 本地 Alpha | 本地完成，draft PR #2 待合入 | 2026-07-23 |
| [v0.3-windows-data-gate](v0.3-windows-data-gate/README.md) | 以 Windows + 官方 TdxQuant 取得只读真实数据、授权和共享核心结论 | 进行中（HAZ-526 后继候选待 Windows live readback 与 ≥30 分钟 M0） | |
| [v0.3.1-windows-tushare-data-gate](v0.3.1-windows-tushare-data-gate/README.md) | 跨平台 Tushare 兼容 HTTP 数据闸门，Windows 优先 | 进行中 | |
| [v0.4-v1-feature-complete](v0.4-v1-feature-complete/README.md) | Windows 全市场真实 Top3、提醒、历史与总结闭环 | 进行中 | |
| [v0.4.1-shared-connection-gate](v0.4.1-shared-connection-gate/README.md) | 从冻结 Windows V1 基线提取的跨平台连接门、能力检测与限流返修 | 本地完成，已由 Mac 分支消费 | |
| [v0.4.2-macos-v1-port](v0.4.2-macos-v1-port/README.md) | 用共享 Tushare 核心完成 Mac 真机数据、平台行为与内部 `.app` | Mac V1 内部试用主线；新鲜固定时点与15:30准点仍待验收 | |
| [v0.4.0-alpha.2-internal-baseline](v0.4.0-alpha.2-internal-baseline/README.md) | 固定 Mac / Web / Windows 可重建内部试用基准 | 源码基准；Web 未接受、Windows 非权威 M0 | 2026-08-11 |
| [v0.5-stabilization](v0.5-stabilization/README.md) | 目标环境稳定化、安装与试用，通过后发布 v1.0.0 | 计划中 | |
| [v0.6-candidate-outcomes](v0.6-candidate-outcomes/README.md) | 下一真实交易日同档理论复盘、统计、历史回补与桌面 UI | 本地实现；真实同点行情待验收 | |

## 候选方向（尚未登记为执行版本）

- 核心稳定后：涨停封单快速下降的独立风险提醒。
- 样本达到门槛后：受限、可回滚的软参数优化与更完整原因详情。

这些方向在启动前再建版本目录、范围和验收；当前不得顺手塞进 v0.1—v0.5。

## 排期原则

Human Owner 于 2026-07-30 改为 Mac-first：先完成 Mac V1 真实候选、交易时段和内部安装
验收；Windows 继续保留 `FAIL`，后续只同步共享修复并在目标 Windows 单独验收。不可用 Mac
结果替代 Windows、TdxQuant/M0、Windows 通知或安装包证据。
