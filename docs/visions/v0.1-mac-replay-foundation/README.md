# v0.1-mac-replay-foundation：在 Mac 建立可回放工程基础

> 状态：本地完成，待同步
> 创建：2026-07-22 ｜ 计划 tag：`v0.1.0`

## 目标与范围

- 在当前 Mac 上建立可复现的 Python 工程、依赖、配置、日志和 SQLite 基础。
- 定义不依赖通达信字段的 domain 对象与 Provider Protocol。
- 实现 MockProvider、ReplayProvider 和 SyntheticScenarioBuilder，让后续规则不依赖真实行情也能确定性验证。
- 建立 pytest、lint、类型检查和固定种子/固定时钟测试入口。
- 可做一个最小 PySide6 smoke 原型，只展示模拟健康状态和模拟 Top3；不实现 Windows 通知和正式交互。
- 本版不接真实通达信、紫黄线、交易账户、Bark、盘中生产调度、Windows 安装包或自动调参。

## 开工门

- [x] 创建并关联 Stage 2 执行 issue：`HAZ-384`（仅做 Mac 可验证基础，不把模拟结果当实时行情）。
- [x] 本版本在 `docs/visions/README.md` 登记为活跃并写入本地 `main`。
- [x] 已记录环境并读取 `rules/data.md`、`rules/storage.md`、`rules/security.md` 与 `boundaries.md`：macOS 27.0（26A5388g）、arm64、Python 3.14.6、uv 0.9.26。
- [x] 从本地 `main` 建实现分支；本版不要求先操作 GitHub。

## 验收标准（必须可检查）

- [x] 全新本地虚拟环境可按锁定依赖安装，启动命令和验证命令写入 `README.md` 与 `AGENTS.md`。
- [x] domain 对象与 Provider Protocol 不引用通达信专有字典键或 Windows 专有路径。
- [x] Mock/Replay/Synthetic 覆盖正常、stale、STOPPED、WARMING、重复时间戳和重连基线场景；STOPPED 不受 `code + source_ts` 去重影响，重连须完成 3 个新鲜 WARMING 样本后才可回到 HEALTHY。
- [x] 相同输入、配置、时钟和随机种子得到完全相同的输出。
- [x] SQLite/配置版本基础可创建、读写和回滚测试，不保存密钥或交易账户信息。
- [x] pytest、lint、类型检查、锁文件检查和 workspace validation 全部通过。
- [x] 统一日志初始化提供脱敏与滚动入口；日志不记录密钥、交易账户或真实用户数据。
- [x] 直接与开发依赖均已登记用途、许可证与安全影响，`uv.lock` 可复现。
- [ ] 若包含 PySide6 smoke，能在当前 Mac 启动并明确显示“模拟数据”；无界面也不能阻塞本版核心验收。
- [x] 文档明确列出未验证项：真实行情、紫黄线、Windows 通知/多屏/托盘、Windows 打包和供应商授权。

## 进度

| 日期 | 进展 | 状态 |
| --- | --- | --- |
| 2026-07-22 | 根据实际只有 Mac 的环境重排首版；尚未启动产品实现 | 计划中 |
| 2026-07-22 | 已激活为进行中版本；开工门已完成，Stage 2 执行 issue 为 `HAZ-384` | 完成 |
| 2026-07-22 | HAZ-384 在 `feat/HAZ-384-mac-replay-foundation` 建立 Python、Mock/Replay/Synthetic、SQLite WAL 与确定性测试；Mac 本地全套校验通过 | 完成 |
| 2026-07-22 | HAZ-388 修复 STOPPED/重连与 Shanghai 时间契约，补日志脱敏滚动和依赖审计；仅 Mac Mock/Replay 回归 | 完成 |
| 2026-07-23 | HAZ-391 将 STOPPED 的 `source_ts` 设为恢复截止线；拒绝并计数所有不晚于截止线的延迟样本，Mac Mock/Replay 回归通过（workspace 校验受既有 AGENTS 运行时链接阻塞） | 完成 |
| 2026-07-23 | Stage 7 PASS 后，本地 `main` 快进合入审定提交 `0083e39`；全套门禁与新 worktree 回读通过 | 本地完成，待同步 |

## 决策与风险

| 决策/风险 | 理由/应对 |
| --- | --- |
| 先做 Provider 边界与 Replay，而不是伪接真实行情 | 真实 Windows/通达信环境不可用，但可测试架构、确定性和失败状态 |
| v0.1 只证明 Mac 本地工程 | 防止把跨平台代码可运行误报成真实数据/目标机可用 |
| GitHub 不作为开工门 | 用户选择 local-first；每个 session 以本地提交和验证保证可恢复 |

## Session Handoff 索引

尚无。执行中优先更新本 README；跨 session 未完成项必须写清本地 commit、下一步、验证与未同步状态。

## 封版记录

- 关联与门禁：Stage 2 `HAZ-384` 实现，Stage 4 `HAZ-388` 定向修复，Stage 6 `HAZ-391` STOPPED 时间门修复，Stage 7 `HAZ-390` 对 `0083e39bea124f8b854192cff34c7c579cf8a532` 给出 PASS，Stage 8 `HAZ-387` 本地合入。
- 本地终态：`main` 包含审定提交 `0083e39bea124f8b854192cff34c7c579cf8a532` 与本封版文档提交；GitHub `origin/main` 仍停在 `7ec5c6b`，尚未同步，未创建 tag 或 PR。
- 验证结果：Mac + Mock/Replay 下 `uv sync --all-groups --frozen`、`uv lock --check`、`uv run pytest`、`uv run ruff check .`、`uv run mypy src tests`、`python3 scripts/validate_workspace.py`、`git diff --check` 均通过，并已在新本地 worktree 回读后复跑核心 pytest。
- 环境与未验证项：只证明 Mac + Mock/Replay；未验证真实行情、通达信紫黄线、供应商授权、Windows 通知/多屏/托盘、Windows 打包。真实数据与目标环境由 v0.3 承接，Mac 本地 Alpha 由 v0.2 承接。
- 同步差异：封版记录提交前后均以 `git rev-list --left-right --count main...origin/main` 回读；本轮证据提交落入 `main` 后预期为 `10 0`（本地领先 10、远端领先 0）。仅可在用户要求的版本节点创建 `publish/v0.1.0` 并同步，当前不 push。
