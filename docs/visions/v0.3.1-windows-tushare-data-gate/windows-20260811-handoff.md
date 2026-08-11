# Windows Tushare V1 交接（2026-08-11）

> 结论：`WINDOWS_SMOKE_PASS`；应用已达到 Windows 内部试用可启动、可保存凭据、可准备
> 基础数据的状态。Human Owner 已报告交易日实际使用基本可用，但仓库内没有本轮连续
> 30 分钟 M0 的脱敏逐轮指标，因此不得据此写成权威 `M0 PASS` 或商业发布完成。

## 1. 交接基线

- 仓库：`HazelKahlil/StockWatcher`。
- 远端基线：`origin/main@6b7936f795fb93e0babebb7bd70b6c767cadd83e`。
- 基线 parent：`67ba7b73aee4ea8b21a8d8d4badcd1129e2e066c`、
  `30595f9f2d4a462b87cee390f8631e1a97158fca`。
- 发布分支：`publish/v0.3.1-windows-smoke`。
- 原执行分支：`windows/internal-test-v1`；没有使用旧 `fix/HAZ-418-blockers`。
- 当前正常数据路线：Tushare Primary Pro + `tushare.realtime_quote(src="sina")`。
- TdxQuant 只保留可选诊断；本轮没有启动 `TdxW.exe`，也没有把 TQ 作为启动门。

## 2. Windows 环境

| 项目 | 现场值 |
| --- | --- |
| Windows | Windows 11 Home 10.0.26200，x64 |
| Python | 3.12.10 |
| PowerShell | 7.6.4 |
| uv | 0.11.30 |
| PyInstaller | 6.21.0 |
| GitHub CLI | 2.97.0 |

没有关闭 Defender、防火墙、Application Control 或 PowerShell 执行策略，没有使用管理员
权限绕过失败。GitHub 直连在本机超时，发布操作仅对相关进程临时使用已启用的本机系统代理；
没有修改全局代理配置。

## 3. 本 PR 的源码改动

### 3.1 Windows 依赖同步

`pyobjc-framework-cocoa` 原为无平台条件的直接依赖，Windows 执行
`uv sync --all-groups` 时会尝试构建 PyObjC，并以 `PyObjC requires macOS to build`
失败。现在依赖只在 `sys_platform == 'darwin'` 时启用，并由 `uv` 更新锁文件。

这不删除 Mac 依赖，也不改变 Tushare、候选、提醒或业务规则。锁文件同时包含当前
`uv` 对若干传递依赖 marker 的规范化结果，review 时应与 `pyproject.toml` 一起核对。

### 3.2 Windows SQLite 损坏恢复

损坏库恢复原先用 `Path.replace()` 把主库重命名为 `.corrupt`。Windows 上若 SQLite
外部句柄仍保持打开，会得到 `PermissionError: [WinError 32]`，恢复流程在保存损坏副本前
中断。

现在仅在 `PermissionError` 时采用保守回退：先 `copy2` 保存 `.corrupt`，再从已验证备份
覆盖恢复。其他异常不吞掉，原始备份选择、完整性检查、只读降级和审计字段保持不变。
测试、断言和 fixture 没有为此修改。

## 4. 当前验证证据

以下均在上述 Windows 真机、发布分支工作树执行；默认 pytest 不包含 `live_tushare`：

| 命令 / 检查 | 结果 | 说明 |
| --- | --- | --- |
| `uv sync --all-groups` | exit `0` | 67 packages resolved，56 packages checked |
| `uv run pytest` | exit `1` | `369 passed, 3 skipped, 2 deselected, 3 failed` |
| Windows/Tushare 目标 pytest 集 | exit `0` | 143 项通过 |
| `uv run ruff check .` | exit `0` | 全部通过 |
| `uv run mypy src tests` | exit `1` | 仅 `ui/macos.py` 的 Foundation/ignore 两项 |
| `uv run python scripts/validate_workspace.py` | exit `0` | 29 个必需文件通过 |
| `uv run python scripts/check_windows_package.py` | exit `0` | offline package contract 通过 |
| `git diff --check` | exit `0` | 通过 |
| PyInstaller Windows bundle | exit `0` | `StockWatcher.exe` 成功生成 |

全量 pytest 的 3 个失败全部位于 `tests/test_macos_port.py`：

1. `test_single_instance_guard_wakes_existing_window`
2. `test_single_instance_guard_does_not_silently_exit_without_ack`
3. `test_single_instance_guard_reports_version_conflict_without_replacing_primary`

它们在 Windows 上执行 macOS 单实例实现并得到第二实例也能 acquire 的结果。本 PR 没有
删除、跳过或修改这些测试。全量 Mypy 的两项错误同样来自 Windows 环境无法导入 macOS
`Foundation`。下一位 owner 应决定由测试平台 marker、条件导入或 CI matrix 处理，不能把
它们伪装成全量工程门通过。

SQLite 损坏恢复测试已进入全量 pytest 的通过项；本轮修复前的首个 Windows 失败
`WinError 32` 已保留在交接说明中，没有通过重复运行掩盖。

## 5. 应用安装与真实启动

- PyInstaller bundle 是本机生成物，不进入 Git。
- bundle 已移到当前用户的 `%LOCALAPPDATA%\Programs\StockWatcher`。
- 桌面快捷方式为 `StockWatcher Windows.lnk`，指向上述 bundle。
- 移动后的打包应用重新启动成功，窗口标题为 `StockWatcher · 当前观察`，进程响应正常。
- 启动过程没有出现 UAC，没有新增 `TdxW.exe`，关闭后没有 StockWatcher 残留进程。
- 本机没有 Inno Setup，因此没有生成或验证 Inno Setup 安装器，也没有宣称安装/卸载/回滚
  已通过；当前交付形态是 portable bundle + 普通用户桌面快捷方式。
- 构建缓存已移出源码工作树保留；`validate_workspace.py` 随后恢复通过。

本机 bundle 与桌面快捷方式只是现场使用资产，不是源码真源。下一位开发者应从 PR commit
重新构建，不复制此机器上的 `build/`、`dist/`、数据库、缓存或日志。

## 6. 凭据与数据状态

- Human Owner 只在应用隐藏输入框内输入并确认保存 Tushare Primary 凭据。
- 只读检查确认 Windows 安全存储中存在 Primary 凭据条目；没有读取或回显凭据值。
- 非秘密配置显示 `mode=tushare_15000`、Primary profile enabled。
- 保存后全市场基础缓存得到更新，说明基础连接、保存和基础数据准备链路已实际运行。
- Human Owner 于 2026-08-11 报告交易日测试完毕、整体基本可用；没有把用户名、完整用户
  目录、行情响应体、HTTP body、持仓、订单或交易账户数据写入仓库。

上述事实足以支持 `WINDOWS_SMOKE_PASS` 和内部试用交接，但不足以单独证明连续 30 分钟
实时 M0 的 p50/p95、错误率、停滞恢复、逐行新鲜度和三周期预热全部达标。测试凭据若曾在
安全 UI 以外暴露，应由 Human Owner 在下一次正式验收前轮换。

## 7. 明确未完成项

1. 仓库内没有本轮连续至少 30 分钟、可审计且脱敏的交易时段 M0 报告。
2. 没有在本轮重新完成一个完整交易日、09:45/14:45、强异动与 15:30 总结的全套证据。
3. Windows 通知、多屏、冷启动、睡眠/断网恢复仍需目标机专项验收。
4. Inno Setup 安装、卸载、回滚和签名包未验证。
5. 全量 pytest/Mypy 仍需把 macOS-only 检查与 Windows matrix 正确隔离。
6. PR 合并前需要在 macOS 主环境确认 Darwin 依赖仍可正常锁定和安装。

这些欠项不阻塞本次 Windows smoke 修复回传，但阻塞将版本表述为商业稳定发布或权威
Windows M0 完成。

## 8. PR review 重点

1. 确认 PyObjC marker 只限制 Windows，不会让 Darwin 丢失 Cocoa 依赖。
2. 在 macOS 执行 `uv lock --check`、`uv sync --all-groups` 和全量工程门。
3. 复核 SQLite `PermissionError` 回退仍保留损坏副本，且不吞掉其他 I/O 错误。
4. 确认 PR 没有测试删除、断言放宽、secret、运行数据库、日志、行情缓存或 bundle。
5. 合并后更新 Mac 本地权威 `main`，再从新的 Windows fresh clone 重建，不复制本机产物。

## 9. 下一位 Agent 的直接执行顺序

1. 读取 `AGENTS.md`、`docs/README.md`、`docs/project/index.md`、
   `docs/process/index.md`、`docs/process/rules/security.md`、
   `docs/process/rules/storage.md`、本版本 README 和本文件。
2. fetch Draft PR，核对 base 为 `main@6b7936f`、head 为
   `publish/v0.3.1-windows-smoke`，先 review diff，不读取任何现场凭据或运行数据。
3. 在 macOS 验证 Darwin 依赖和全量工程门；在 Windows 复跑本文件第 4 节命令。
4. 不把 TdxQuant 恢复为正常启动门，不启动 `TdxW.exe`，不连接券商或调用下单接口。
5. 如需提升为正式 Windows M0，另起明确验收任务并输出脱敏指标，不改写本 handoff 的
   smoke 结论。

## 10. 可复制交接提示词

```text
你现在接手 StockWatcher 的 Windows Tushare V1 回传审查。

仓库：HazelKahlil/StockWatcher
Base：main@6b7936f795fb93e0babebb7bd70b6c767cadd83e
Head：publish/v0.3.1-windows-smoke

先完整阅读 AGENTS.md、docs/README.md、docs/process/index.md、
docs/process/rules/security.md、docs/process/rules/storage.md、
docs/visions/v0.3.1-windows-tushare-data-gate/README.md 和
docs/visions/v0.3.1-windows-tushare-data-gate/windows-20260811-handoff.md。

本 PR 只回传两个 Windows 可移植性修复：PyObjC 限定 Darwin，以及 SQLite 损坏恢复遇到
WinError 32 时保存 .corrupt 副本后从备份恢复；不要改业务规则、测试断言、锁定需求或数据
供应商路线。正常入口是 Tushare，TdxQuant 仅为可选诊断。

请先 review diff 和 secret/二进制边界，再在 macOS 验证 uv lock/sync、pytest、ruff、mypy、
workspace validator；Windows 结果按 handoff 逐项复核。不得读取或索取 Token、账号、持仓、
订单、HTTP body，不得连接券商、下单、绕过 UAC/Defender/执行策略，也不得把 Human Owner
“基本可用”的反馈写成权威 30 分钟 M0 PASS。

最终只给出：可否合并、P0/P1/P2 review 发现、各命令 exit code、仍需 owner 的验收项，
以及合并后 Mac 本地 main 与 Windows fresh clone 的同步步骤。
```
