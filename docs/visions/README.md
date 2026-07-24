# 版本索引

> 最后更新：2026-07-23
> 一个工作版本一个目录，内含 `README.md`。产品版本与 V2.0 规格文档版本不要混淆。

## 活跃版本

- **[v0.3-windows-data-gate](v0.3-windows-data-gate/README.md)** — Windows + 官方 TdxQuant 单人只读数据闸门；Mac 只做 Mock/Replay 与离线契约验证（状态：进行中；等待 Windows 现场 M0）。

## 同步状态

- 日常权威源：本地 `main`。
- GitHub：单一 draft PR #2 已承接本地 v0.1—v0.3 前置交付候选；`origin/main` 仍是最近一次已合入的里程碑镜像。
- 在里程碑 PR 合入前，必须把远端状态表述为“尚未同步”，不能用本地完成代替 GitHub 交付。

## 版本列表

| 版本 | 目标 | 状态 | 封版日期 |
| --- | --- | --- | --- |
| [v0.0-project-bootstrap](v0.0-project-bootstrap/README.md) | 建立仓库、治理骨架、版本路线并迁入 V2.0 交接基线 | 已封版 | 2026-07-22 |
| [v0.1-mac-replay-foundation](v0.1-mac-replay-foundation/README.md) | 在 Mac 建立可复现的跨平台工程与 Replay 基础 | 本地完成，draft PR #2 待合入 | 2026-07-23 |
| [v0.2-mac-local-alpha](v0.2-mac-local-alpha/README.md) | 交付基于 Mock/Replay 的 Mac 本地 Alpha | 本地完成，draft PR #2 待合入 | 2026-07-23 |
| [v0.3-windows-data-gate](v0.3-windows-data-gate/README.md) | 以 Windows + 官方 TdxQuant 取得只读真实数据、授权和共享核心结论 | 进行中（前置交付包已实现，待真机 M0） | |
| [v0.4-v1-feature-complete](v0.4-v1-feature-complete/README.md) | 在真实数据路线基础上接入完整 V1 功能 | 计划中 | |
| [v0.5-stabilization](v0.5-stabilization/README.md) | 目标环境稳定化、安装与试用，通过后发布 v1.0.0 | 计划中 | |

## 候选方向（尚未登记为执行版本）

- 核心稳定后：涨停封单快速下降的独立风险提醒。
- 样本达到门槛后：受限、可回滚的软参数优化与更完整原因详情。

这些方向在启动前再建版本目录、范围和验收；当前不得顺手塞进 v0.1—v0.5。

## 排期原则

先 Mac Replay 技术基础 → Mac 本地 Alpha → Windows/TdxQuant 真实环境数据闸门 → 完整 V1 → 稳定化与安装 → 常规迭代。Mac 结果不证明 Windows、通达信或紫黄线能力；v0.3 只在真实 Windows 现场形成 TdxQuant M0 结论。
