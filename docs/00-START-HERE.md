# StockWatcher 从这里开始

## 先读什么

1. `AGENTS.md`
2. `PROJECT_INDEX.md`
3. `CURRENT_RELEASES.json`
4. `docs/README.md`
5. `docs/project/index.md`
6. `docs/process/index.md`、`docs/process/boundaries.md` 与命中的安全/存储规则
7. `docs/visions/README.md`
8. `docs/visions/v0.4.2-macos-v1-port/README.md`
9. `docs/visions/v0.4.1-shared-connection-gate/README.md`
10. `docs/01-CURRENT-STATUS.md` 与对应 `docs/tracks/*.md`

## 权威事实

- 唯一开发目录：`~/Documents/700-AI-Workspace/20-Projects/StockWatcher`。
- 唯一共享/App 基线：本地 `main` 的 `88ccf49f91fa814af83a004232315286feca3fb7`。
- 已安装 App 的 `SOURCE_COMMIT` 与该 main HEAD 一致；不要覆盖、重装或删除它。
- Web 是独立 `web/internal-test-v1` 线，HEAD `87a8b85609f57504861e09f416694582556b736e`，不合入 main。
- Windows 是规划/历史线，不是当前开发 worktree。

## 不要从哪里继续开发

- 不从 Downloads 中的 ZIP 或解压目录开发。
- 不从 `90-Archive` 的历史解压包恢复一个新的长期源码副本。
- 不把 App、Bundle、handoff、日志、数据库备份当作源码真源。
- `import/rc4-strict-audit` 虽保留历史分支，但其 patch 与 main 已有 RC4 修复等价；不要再次 cherry-pick。

## 轨道边界

- Shared Core 只保留一份；CandidateEngine、StableTop3、StrongMovementDetector、调度与 SQLite 逻辑不得复制。
- Mac UI/Keychain/生命周期/路径/通知只在平台层；普通入口不依赖 TdxQuant。
- Web 只通过 headless service/adapter 消费 Shared Core，唯一 Worker 承担扫描和自动任务。
- Web 当前为 `blocked/not_accepted`：pytest 当前现场通过不等于 VPS、域名、真实 Token 或完整交易日通过。
- Windows 真正开始时，从已验证 main HEAD 临时创建 `windows/internal-test-v1`，只做 Credential Manager、通知、路径、单实例、自动启动、安装和独立 Live 验收。

## 凭据与数据

- 不读取交易密码、交易账户、持仓、订单或 Keychain 内容。
- 不把 Token 写入配置、SQLite、日志、截图、Bundle、ZIP 或命令行。
- 不访问 GitHub；本地 main 是当前工作事实源。
