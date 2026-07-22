# 版本索引

> 最后更新：2026-07-22
> 一个工作版本一个目录，内含 `README.md`。产品版本与 V2.0 规格文档版本不要混淆。

## 活跃版本

- 无。`v0.0-project-bootstrap` 已完成；产品实现尚未启动。

## 下一目标版本

- **[v0.1-m0-data-gate](v0.1-m0-data-gate/README.md)** — 验证通达信数据、紫黄线、板块、历史、性能、重连与授权（状态：计划中）。
- 首个实现 issue 进入 `todo/in_progress` 前，必须先把本节目标移入“活跃版本”，同步版本 README 为“进行中”，并让变更先进入 `main`。

## 版本列表

| 版本 | 目标 | 状态 | 封版日期 |
| --- | --- | --- | --- |
| [v0.0-project-bootstrap](v0.0-project-bootstrap/README.md) | 建立仓库、治理骨架、版本路线并迁入 V2.0 交接基线 | 已封版 | 2026-07-22 |
| [v0.1-m0-data-gate](v0.1-m0-data-gate/README.md) | 完成 M0 数据与授权可行性结论 | 计划中 | |
| [v0.2-alpha-core](v0.2-alpha-core/README.md) | 交付可回放的 M1 Alpha 核心流程 | 计划中 | |
| [v0.3-v1-feature-complete](v0.3-v1-feature-complete/README.md) | 接入完整 V1 功能，但尚未宣称稳定发布 | 计划中 | |
| [v0.4-stabilization](v0.4-stabilization/README.md) | 稳定化、目标机安装与试用，通过后发布 v1.0.0 | 计划中 | |

## 候选方向（尚未登记为执行版本）

- 核心稳定后：涨停封单快速下降的独立风险提醒。
- 样本达到门槛后：受限、可回滚的软参数优化与更完整原因详情。

这些方向在启动前再建版本目录、范围和验收；当前不得顺手塞进 v0.1—v0.4。

## 排期原则

先数据可行性 → 再 Alpha 核心流程 → 后完整 V1 → 稳定化与安装 → 常规迭代。方向未定的部分留作候选，不提前硬设计。
