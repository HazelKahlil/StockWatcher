# v0.1-mac-replay-foundation：在 Mac 建立可回放工程基础

> 状态：计划中
> 创建：2026-07-22 ｜ 计划 tag：`v0.1.0`

## 目标与范围

- 在当前 Mac 上建立可复现的 Python 工程、依赖、配置、日志和 SQLite 基础。
- 定义不依赖通达信字段的 domain 对象与 Provider Protocol。
- 实现 MockProvider、ReplayProvider 和 SyntheticScenarioBuilder，让后续规则不依赖真实行情也能确定性验证。
- 建立 pytest、lint、类型检查和固定种子/固定时钟测试入口。
- 可做一个最小 PySide6 smoke 原型，只展示模拟健康状态和模拟 Top3；不实现 Windows 通知和正式交互。
- 本版不接真实通达信、紫黄线、交易账户、Bark、盘中生产调度、Windows 安装包或自动调参。

## 开工门

- [ ] 创建并关联执行 issue，明确本版只做 Mac 可验证基础，不把模拟结果当实时行情。
- [ ] 本版本在 `docs/visions/README.md` 登记为活跃并写入本地 `main`。
- [ ] 记录 macOS、芯片、Python 和包管理器版本；读取 `rules/data.md`、`rules/storage.md`、`rules/security.md` 与 `boundaries.md`。
- [ ] 从本地 `main` 建实现分支；本版不要求先操作 GitHub。

## 验收标准（必须可检查）

- [ ] 全新本地虚拟环境可按锁定依赖安装，启动命令和验证命令写入 `README.md` 与 `AGENTS.md`。
- [ ] domain 对象与 Provider Protocol 不引用通达信专有字典键或 Windows 专有路径。
- [ ] Mock/Replay/Synthetic 覆盖正常、stale、STOPPED、WARMING、重复时间戳和重连基线场景。
- [ ] 相同输入、配置、时钟和随机种子得到完全相同的输出。
- [ ] SQLite/配置版本基础可创建、读写和回滚测试，不保存密钥或交易账户信息。
- [ ] pytest、lint、类型检查和 workspace validation 全部通过。
- [ ] 若包含 PySide6 smoke，能在当前 Mac 启动并明确显示“模拟数据”；无界面也不能阻塞本版核心验收。
- [ ] 文档明确列出未验证项：真实行情、紫黄线、Windows 通知/多屏/托盘、Windows 打包和供应商授权。

## 进度

| 日期 | 进展 | 状态 |
| --- | --- | --- |
| 2026-07-22 | 根据实际只有 Mac 的环境重排首版；尚未启动产品实现 | 计划中 |

## 决策与风险

| 决策/风险 | 理由/应对 |
| --- | --- |
| 先做 Provider 边界与 Replay，而不是伪接真实行情 | 真实 Windows/通达信环境不可用，但可测试架构、确定性和失败状态 |
| v0.1 只证明 Mac 本地工程 | 防止把跨平台代码可运行误报成真实数据/目标机可用 |
| GitHub 不作为开工门 | 用户选择 local-first；每个 session 以本地提交和验证保证可恢复 |

## Session Handoff 索引

尚无。执行中优先更新本 README；跨 session 未完成项必须写清本地 commit、下一步、验证与未同步状态。

## 封版记录

- 验证结果：待执行。
- 遗留问题：真实数据与目标环境由 v0.3 承接；Mac 本地 Alpha 由 v0.2 承接。
- 终态对账：关联 issue 待创建；必需子任务/stage 待登记；本地/远端同步状态待填写。
