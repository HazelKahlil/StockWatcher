# v0.4.0-alpha.2：Mac / Web / Windows 内部试用基准

> 状态：内部试用源码基准；不是商业稳定发布、权威 M0 或三平台完整验收。
> 创建：2026-08-11
> Python 版本：`0.4.0a2`
> 封版 tag：`v0.4.0-alpha.2`

## 目标

把当前 Shared Core、Mac、Web 与 Windows 已经实际使用和验证过的状态固定为一个可检索、
可重建、可回滚的里程碑。后续修改从该 tag 之后继续，不再依赖聊天记录、旧安装资产或
临时 worktree 猜测基线。

本版本只固定事实和源码，不把不同平台的证据相互替代：

- Shared Core / Mac 源码以本地 `main` 应用代码提交
  `ad04e392158c7050f84e0318fe1d53aaa0370c34` 为基准。
- Web 固定为独立分支 `web/internal-test-v1@bf447ba62957e3d12a766df26c980b96ad4c74b2`，
  不把其独有实现硬合入 `main`。
- Windows 可移植性和安全恢复来自 PR #4 merge commit
  `a5da2705ed8c6b8d9670d0b6dbc751018f78828c`，并已进入 Shared Core 基准。

## 三轨状态

| 轨道 | 固定内容 | 当前结论 |
| --- | --- | --- |
| Shared Core / Mac 源码 | Tushare 主路线、确定性候选、稳定 Top3、强异动、调度、历史/总结、SQLite 安全恢复及 `0.4.0a2` 版本元数据 | `internal_trial_source_baseline` |
| 已安装 Mac App | 现有 arm64 ad-hoc App 保持原样，已记录 `SOURCE_COMMIT=88ccf49f...`；本轮没有覆盖、重装或读取 Keychain/运行数据 | `internal_trial`，安装资产早于 alpha.2 源码 |
| Web | `bf447ba`；Mac Docker 的 Web、Worker、Caddy/Tunnel 当前运行，公网首页返回 HTTP 200 | **`BLOCKED / NOT_ACCEPTED`** |
| Windows | PR #4 已合并；Windows 3.11/3.12 CI、Setup、Preflight、PyInstaller、Inno 与制品上传通过 | `WINDOWS_SMOKE_PASS`，不是权威 M0 |

## 验证证据

### Shared Core / macOS 源码

- `uv lock --check`：exit `0`。
- `uv sync --all-groups --frozen`：exit `0`。
- `uv run pytest`：exit `0`，`363 passed, 20 skipped, 2 deselected`。
- `uv run ruff check .`：exit `0`。
- `uv run mypy src tests`：exit `0`，109 source files。
- workspace validator：exit `0`，29 个必需文件。
- Windows package contract 与 `git diff --check`：exit `0`。

### Web 独立工作线

- HEAD 与工作区：`bf447ba`，clean。
- pytest：exit `0`，`434 passed, 25 skipped, 2 deselected`。
- Ruff、Mypy（136 source files）、workspace validator（29 个必需文件）与 JavaScript
  syntax check：exit `0`。
- 只读运行观察：Web、Worker、Caddy/Tunnel 容器运行；有 healthcheck 的容器为 healthy；
  `https://stock.hazelkahlil.com/` 返回 HTTP 200。

这些是当前 Mac Docker 与公网可达证据，不解除 Web 的交易日、通知、重放、备份恢复和
持续运行验收门，也不把 Mac 主机托管冒充 VPS。

### Windows 回传

- 源码 Governance run `31478494946`：Python 3.11 与 3.12 均为
  `373 passed, 6 skipped, 2 deselected`，Ruff、Mypy、PowerShell Setup、fail-closed
  Preflight、package contract、PyInstaller、Inno Setup 与 artifact upload 全部通过。
- 最终 PR 文档 head run `31479040103`：workspace-integrity 和两个 Windows 矩阵全绿。
- Human Owner 的交易日“基本可用”反馈只支持内部试用语境，不替代连续 30 分钟脱敏 M0。

## 已知边界

1. 已安装 Mac App 没有从 `0.4.0a2` 重建；需要分发新包时应从本 tag 重新构建并生成新的
   `SOURCE_COMMIT`，不能沿用旧 App 的哈希冒充 alpha.2。
2. Web 必须继续显示 `BLOCKED / NOT_ACCEPTED`。浏览器完全关闭后的 Web Push 未实现，
   当前线上依赖本机 Mac、网络和 Docker Desktop 持续运行。
3. Windows 目标机现有 portable 早于最终 SQLite 安全恢复和 alpha.2 打包元数据；需要新包时
   从本 tag fresh clone 重建。连续 M0、完整交易日和目标机安装/卸载/回滚仍未验收。
4. GitHub Actions 当前有 Node.js 20 action 弃用提示；runner 强制 Node.js 24 后通过，后续
   作为独立维护项升级 action 主版本。
5. 本版本不包含 VPS、中国大陆网络或额外 Windows 现场验收，也不改变只读候选观察边界。

## 后续开发与回滚

- 后续修改从 `v0.4.0-alpha.2` 对应提交继续；不要从旧 App、Web 归档 ZIP 或 Windows
  portable 反向恢复源码。
- 重建 Shared Core / Mac 前执行 `uv sync --all-groups --frozen` 与完整工程门。
- Web 修复继续在 `web/internal-test-v1` 或其明确后继分支推进，保持与 main 的独立 provenance。
- 需要回滚时先回到本 tag；数据库回滚仍须先备份并遵守 SQLite 完整性与只读降级规则。
