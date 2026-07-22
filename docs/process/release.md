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
| `v0.1-m0-data-gate` | M0 数据可行性 | `v0.1.0` 或明确 FAIL/PASS_WITH_LIMITS 结论 |
| `v0.2-alpha-core` | M1 可运行 Alpha | `v0.2.0` |
| `v0.3-v1-feature-complete` | M2 完整 V1 功能 | `v0.3.0`，尚不等于稳定发布 |
| `v0.4-stabilization` | M3 稳定化、安装与试用 | 通过后发布 `v1.0.0` |

后续风险异动与自动软参数优化是候选方向，不在实现启动前预先承诺 tag。

## 封版门

1. 回读关联 issue 的 Done when、必需子任务/stage 和目标版本 README。
2. 所有必需验收有真实证据；依赖 Windows、通达信或授权的事项不能用 CI 替代。
3. 自动测试、lint、类型检查、Replay/现场 smoke 按版本要求通过。
4. 配置、Schema、供应商版本、安全和降级行为已记录；未完成项有明确 successor 和 owner。
5. 更新版本 README 状态、`CHANGELOG.md`、必要的长期事实和运行手册。
6. 从 `main` 的已验证提交创建签名或 annotated tag，并核对 GitHub Actions。

任一必需项缺失时保持进行中或阻塞；代码合并本身不算封版。

## Changelog 规则

- `Added`：新能力；`Changed`：用户可见或规则行为变化；`Fixed`：缺陷；`Security`：安全修复；`Deprecated/Removed`：淘汰。
- 阈值、数据源、通达信兼容版本、Schema 和安全边界变化必须记录。
- 纯内部重排且不影响使用/维护者恢复状态时不制造噪声条目。

## 回滚

- 代码与配置回滚到最近稳定 tag；配置必须保留历史版本。
- Schema 回滚先保护数据，使用备份或兼容读取，不直接删除真实数据。
- 数据源异常时关闭对应能力并降级，不回退到未经授权或未验证的抓取路线。
