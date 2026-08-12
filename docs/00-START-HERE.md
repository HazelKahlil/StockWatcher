# StockWatcher 从这里开始

## 先读什么

1. `AGENTS.md`
2. `PROJECT_INDEX.md`
3. `CURRENT_RELEASES.json`
4. `docs/README.md`
5. `docs/project/index.md`
6. `docs/process/index.md`、`docs/process/boundaries.md` 与命中的安全/存储规则
7. `docs/visions/README.md`
8. `docs/visions/v0.4.0-alpha.2-internal-baseline/README.md`
9. 当前后继开发读取 `docs/visions/v0.6-candidate-outcomes/README.md`
10. 目标轨道的 `docs/tracks/*.md`

## 权威事实

- 唯一开发目录：`~/Documents/700-AI-Workspace/20-Projects/StockWatcher`。
- 当前应用代码基线：`ad04e392158c7050f84e0318fe1d53aaa0370c34`，Python `0.4.0a2`。
- 里程碑 tag：`v0.4.0-alpha.2`；它是内部试用源码/回滚基准，不是 stable release。
- 已安装 Mac App 保持既有 `SOURCE_COMMIT=88ccf49f...`，早于 alpha.2；本轮没有覆盖或重装。
- Web 是独立 `web/internal-test-v1@bf447ba`，当前 Mac Docker 可达但继续
  `BLOCKED / NOT_ACCEPTED`，不合 main。
- Windows PR #4 已进入 main，结论是 `WINDOWS_SMOKE_PASS`，不是权威 M0。

## 不要从哪里继续开发

- 不从 Downloads 中的 ZIP、旧 App、Web handoff 或 Windows portable 反向恢复源码。
- 不把 `90-Archive` 的历史解压包恢复成新的长期开发真源。
- 不把截图、CI、测试通过或公网可访问单独写成 `ACCEPTED`、正式 M0 或商业稳定。

## 轨道边界

- Shared Core 只保留一份；CandidateEngine、StableTop3、StrongMovementDetector、调度与
  SQLite 逻辑不得复制。
- Mac UI/Keychain/生命周期/路径/通知只在平台层；普通入口不依赖 TdxQuant。
- Web 只通过 headless service/adapter 消费 Shared Core，唯一 Worker 承担扫描和自动任务。
- Windows 后续只做 Credential Manager、通知、路径、单实例、安装和独立 Live 验收；
  需要新包时从 `v0.4.0-alpha.2` fresh clone。

## 凭据与数据

- 不读取交易密码、交易账户、持仓、订单、Keychain 或 Credential Manager 内容。
- 不把 Token 写入配置、SQLite、日志、截图、Bundle、ZIP、命令行或工作记忆。
- 里程碑可按 local-first 发布流程同步 GitHub；日常事实仍以本地 `main` 为准。
