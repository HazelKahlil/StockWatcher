# 版本与发布流程

> 最后更新：2026-07-22
> 目标：让规格版本、开发版本、软件 tag 和 issue 状态不互相冒充。

## 版本口径

- 交接材料的 `V2.0` 是需求文档版本，不代表 StockWatcher 软件已经达到 2.0。
- 软件采用 SemVer。Bootstrap 标记 `v0.0.0`；预发布主线按 `v0.1.0`、`v0.2.0` 推进；稳定交付从 `v1.0.0` 开始。
- `docs/visions/` 目录名表达工作版本与业务目标；tag 表达封版的软件/治理快照。两者映射必须写在版本 README。

| 工作版本 | 交接阶段 | 预期 tag / 结果 |
| --- | --- | --- |
| `v0.0-project-bootstrap` | 新项目治理与材料迁移 | `v0.0.0` |
| `v0.1-mac-replay-foundation` | Mac 工程与可回放基础 | `v0.1.0` |
| `v0.2-mac-local-alpha` | Mac 本地可运行 Alpha | `v0.2.0` |
| `v0.3-windows-data-gate` | Windows/通达信真实 M0 | `v0.3.0` 或明确 PASS/PASS_WITH_LIMITS/FAIL 结论 |
| `v0.4-v1-feature-complete` | 真实数据路线上的完整 V1 功能 | `v0.4.0`，尚不等于稳定发布 |
| `v0.4.0-alpha.2-internal-baseline` | 固定 Mac / Web / Windows 当前内部试用源码与证据边界 | `v0.4.0-alpha.2`，不是权威 M0 或 stable release |
| `v0.5-stabilization` | 目标环境稳定化、安装与试用 | 通过后发布 `v1.0.0` |

后续风险异动与自动软参数优化是候选方向，不在实现启动前预先承诺 tag。

## local-first 同步流程

1. 日常任务在本地短分支完成，验证和 diff review 通过后本地合并到 `main`。
2. session 收尾保证本地 `main` 有可恢复提交，并记录 `git rev-list --left-right --count main...origin/main` 的同步状态。
3. 版本验收完成但尚未同步时，版本状态写“本地完成，待同步”；不能宣称 GitHub 已交付，也不创建正式发布 tag。
4. 需要版本备份/交接时，从本地 `main` 创建 `publish/<version>`，一次性 push，并开一个里程碑 PR。
5. PR 合入后把远端 merge 回读到本地，确认本地与 `origin/main` 对齐，再创建并 push annotated tag。

这个流程把 GitHub 操作压缩到版本节点，同时保留远端备份、可审查历史和可恢复发布点。

## 封版门

1. 回读关联 issue 的 Done when、必需子任务/stage 和目标版本 README。
2. 所有必需验收有真实证据并标注运行环境；依赖 Windows、通达信或授权的事项不能用 Mac/CI 替代。
3. 自动测试、lint、类型检查、Replay/现场 smoke 按版本要求通过。
4. 配置、Schema、供应商版本、安全和降级行为已记录；未完成项有明确 successor 和 owner。
5. 更新版本 README 状态、`CHANGELOG.md`、必要的长期事实和运行手册。
6. 里程碑 PR 合入、local/remote `main` 对齐后，从已验证提交创建签名或 annotated tag，并核对 GitHub Actions 或记录其平台级阻塞。

任一必需项缺失时保持进行中或阻塞；代码合并本身不算封版。

## Changelog 规则

- `Added`：新能力；`Changed`：用户可见或规则行为变化；`Fixed`：缺陷；`Security`：安全修复；`Deprecated/Removed`：淘汰。
- 阈值、数据源、通达信兼容版本、Schema 和安全边界变化必须记录。
- 纯内部重排且不影响使用/维护者恢复状态时不制造噪声条目。

## 回滚

- 代码与配置回滚到最近稳定 tag；配置必须保留历史版本。
- Schema 回滚先保护数据，使用备份或兼容读取，不直接删除真实数据。
- 数据源异常时关闭对应能力并降级，不回退到未经授权或未验证的抓取路线。
