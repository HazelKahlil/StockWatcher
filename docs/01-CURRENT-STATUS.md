# 当前状态

> 核验日期：2026-08-13。当前是内部试用基准，不是商业发布或完整三平台验收。

| 轨道 | 权威代码基线/分支 | 当前证据 | 未完成/阻塞 | 结论 |
| --- | --- | --- | --- | --- |
| 次日同点复盘 | `main@6a81825` / Web 实现 `53501ad`，开发版本 `0.6.0a4`；桌面 SQLite v8 / Web v9 | Web 离线 `509 passed, 26 skipped`，`-W error` 零告警；严格日历、最多五次重试、认证 outcomes API 与迁移门全绿 | 真实下一交易日 09:45/14:45 同点结算仍待现场验收 | `local_code_complete_offline_verified_after_alpha4_reliability_fix` |
| Shared Core | `main` 应用代码 `ad04e392158c7050f84e0318fe1d53aaa0370c34` / `0.4.0a2` | `363 passed, 20 skipped, 2 deselected`；Ruff/Mypy/validator/lock/package contract 全绿 | 真实交易时段证据不能由离线门替代 | `internal_trial_source_baseline` |
| App Mac | 已安装版记录 `SOURCE_COMMIT=88ccf49f...`；重建源码为 `ad04e39` | 既有 arm64/ad-hoc、Keychain、SQLite、PDF、单实例和窗口行为证据保留 | 安装资产未重建为 alpha.2；固定时点、15:30 与真实恢复仍待补验 | `internal_trial` |
| Web | 本地 `cnb/main` 已快进包含实现 `53501ad`，本轮未部署/未 push | `509 passed, 26 skipped`、`-W error` 零告警、定向 Web 契约 `35 passed`；本机 Chrome 已验证桌面布局、右下角关闭、刷新/重开不重放旧提醒；Schema v9 | 本轮部署，以及真实交易日 09:45/14:45 固定提醒、同点结算/重试与全日运行现场验收 | **`BLOCKED / NOT_ACCEPTED`** |
| Windows | PR #4 merge `a5da270`，最终源码基线 `ad04e39` | Windows 3.11/3.12 Governance、Setup/Preflight、PyInstaller/Inno 与制品上传通过；Owner 报告基本可用 | 目标机旧 portable 未从 alpha.2 重建；权威 M0、安装/卸载/回滚与签名包未验收 | `WINDOWS_SMOKE_PASS` |

## 当前禁止的误读

- Web 的 Mac Docker、CI 和公网可达不证明生产稳定，也不解除 `BLOCKED / NOT_ACCEPTED`。
- Mac 的实时 Top3、通知和安装证据不证明 Windows；Windows CI 不证明目标机完整交易日。
- `v0.4.0-alpha.2` 是可重建/回滚版本节点，不是投资建议、自动交易能力或稳定发行批准。
- `0.6.0a4` 的离线工程门、部署和截图不等于真实次日同点行情验收，也不表示已安装 App 已升级。
- 当前 Web 桌面视觉与提醒生命周期 Review 为 `P0=0 / P1=0 / P2=0`；浏览器完全关闭后的 Web Push
  是可选后续能力，不是本轮验收阻断项。
