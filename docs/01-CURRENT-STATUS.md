# 当前状态

> 核验日期：2026-08-11。当前是内部试用基准，不是商业发布或完整三平台验收。

| 轨道 | 权威代码基线/分支 | 当前证据 | 未完成/阻塞 | 结论 |
| --- | --- | --- | --- | --- |
| 次日同点复盘 | `fix/candidate-outcomes-live-contract@d85f378`，开发版本 `0.6.0a2` / SQLite v8 | macOS 离线 `404 passed, 20 skipped, 2 deselected`；受控 DEGRADED trade_cal、有界持久化重试、v7→v8、Ruff/Mypy/validator/lock/package contract/diff 全绿；三态 UI 截图已检查 | 真实下一交易日 09:45/14:45 同点结算、错过时点回补和目标机重建未现场验收 | `local_code_complete_offline_verified_after_contract_fix` |
| Shared Core | `main` 应用代码 `ad04e392158c7050f84e0318fe1d53aaa0370c34` / `0.4.0a2` | `363 passed, 20 skipped, 2 deselected`；Ruff/Mypy/validator/lock/package contract 全绿 | 真实交易时段证据不能由离线门替代 | `internal_trial_source_baseline` |
| App Mac | 已安装版记录 `SOURCE_COMMIT=88ccf49f...`；重建源码为 `ad04e39` | 既有 arm64/ad-hoc、Keychain、SQLite、PDF、单实例和窗口行为证据保留 | 安装资产未重建为 alpha.2；固定时点、15:30 与真实恢复仍待补验 | `internal_trial` |
| Web | `web/internal-test-v1@bf447ba` | `434 passed, 25 skipped, 2 deselected`；Ruff/Mypy/validator/JS 全绿；Mac Docker 容器运行且公网 HTTP 200 | 完整交易日、通知/重放、断线与备份恢复；浏览器关闭后的 Web Push 未实现 | **`BLOCKED / NOT_ACCEPTED`** |
| Windows | PR #4 merge `a5da270`，最终源码基线 `ad04e39` | Windows 3.11/3.12 Governance、Setup/Preflight、PyInstaller/Inno 与制品上传通过；Owner 报告基本可用 | 目标机旧 portable 未从 alpha.2 重建；权威 M0、安装/卸载/回滚与签名包未验收 | `WINDOWS_SMOKE_PASS` |

## 当前禁止的误读

- Web 的 Mac Docker、CI 和公网可达不证明生产稳定，也不解除 `BLOCKED / NOT_ACCEPTED`。
- Mac 的实时 Top3、通知和安装证据不证明 Windows；Windows CI 不证明目标机完整交易日。
- `v0.4.0-alpha.2` 是可重建/回滚版本节点，不是投资建议、自动交易能力或稳定发行批准。
- `0.6.0a2` 的离线工程门与截图不等于真实次日同点行情验收，也不表示已安装 App 已升级。
