# 当前状态

> 核验日期：2026-08-12。当前是内部试用基准，不是商业发布或完整三平台验收。

| 轨道 | 权威代码基线/分支 | 当前证据 | 未完成/阻塞 | 结论 |
| --- | --- | --- | --- | --- |
| 次日同点复盘 | `fix/candidate-outcomes-p1-reliability@ed63106`，开发版本 `0.6.0a4` / SQLite v8 | macOS 离线 `417 passed, 20 skipped, 2 deselected`；严格 `/trade_cal + DEGRADED/MISSING`、0/1/布尔开市标记、最多五次持久化重试、Ruff/Mypy/validator/lock/package contract/secret/diff 全绿 | 真实下一交易日 09:45/14:45 同点结算、错过时点回补和目标机安装验收未现场通过 | `local_code_complete_offline_verified_after_alpha4_reliability_fix` |
| Shared Core | `main` 应用代码 `ad04e392158c7050f84e0318fe1d53aaa0370c34` / `0.4.0a2` | `363 passed, 20 skipped, 2 deselected`；Ruff/Mypy/validator/lock/package contract 全绿 | 真实交易时段证据不能由离线门替代 | `internal_trial_source_baseline` |
| App Mac | alpha.4 包源码 `main@b00221f`；已安装版仍记录 `88ccf49f...` | 仓库外 `.app` ZIP/DMG、ad-hoc 签名、DMG 挂载、临时 HOME + Replay 启动/优雅退出均通过 | 新包未覆盖现有 App；固定时点、15:30 与真实恢复仍待补验 | `alpha4_package_verified_not_installed` |
| Web | 独立线 `fix/web-top3-gain-emphasis@9411cd8`；部署镜像源码 `cfc6cd6` / SQLite v9 | `499 passed, 25 skipped, 2 deselected`、`-W error` 零告警；依赖审计无已知漏洞；Morandi UI、Top3 上涨旧版红/放大字号、中央提醒与 Schema v9 全绿；Mac Docker 与公网 live/ready/CSS 通过 | 真实交易日 09:45/14:45 固定提醒、同点结算/重试与全日运行现场验收 | **`BLOCKED / NOT_ACCEPTED`** |
| Windows | alpha.4 fresh-build 输入 `main@b00221f` | 同提交源码 ZIP 和离线 package contract 已验证；历史 `WINDOWS_SMOKE_PASS` 证据保留 | 当前会话无 Windows/PowerShell/Inno，alpha.4 portable/EXE 及安装/卸载未构建，不能用 macOS 冒充 | `WINDOWS_ALPHA4_PACKAGE_BLOCKED_NO_HOST` |

## 当前禁止的误读

- Web 的 Mac Docker、CI 和公网可达不证明生产稳定，也不解除 `BLOCKED / NOT_ACCEPTED`。
- Mac 的实时 Top3、通知和安装证据不证明 Windows；Windows CI 不证明目标机完整交易日。
- `v0.4.0-alpha.2` 是可重建/回滚版本节点，不是投资建议、自动交易能力或稳定发行批准。
- `0.6.0a4` 的离线工程门、截图和新安装包都不等于真实次日同点行情验收；现有已安装 App 不会被本轮覆盖。
- 当前 Web 离线 Review 为 `P0=0 / P1=0 / P2=0`；浏览器完全关闭后的 Web Push
  是可选后续能力，不是本轮验收阻断项。
