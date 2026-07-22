# 参与 StockWatcher 开发

## 开始前

1. 按 `AGENTS.md` 的顺序读取项目事实源。
2. 在 `docs/visions/README.md` 找到活跃或目标版本；任务不在范围内时先调整版本记录，不带着隐含需求开工。
3. 查 `docs/process/index.md` 的规则路由表，阅读任务命中的领域规则。

这样做是为了让需求、实现和验收使用同一份范围锚点，避免跨 session 后靠记忆猜意图。

## 分支与提交

- `main` 始终代表可恢复的项目事实源。Bootstrap 后禁止直接推送。
- 分支格式：`feat/<issue>-<slug>`、`fix/<issue>-<slug>`、`docs/<issue>-<slug>`、`chore/<issue>-<slug>`。
- 一次提交只表达一个可 review 的意图；提交标题用祈使句并带 issue key，例如 `HAZ-000: validate provider metadata`。
- 不提交密钥、用户配置、数据库、日志、行情缓存、安装包或个人绝对路径。

短分支和单意图提交能降低数据规则、UI 和持久化改动相互掩盖的风险，也让回滚更可靠。

## Pull Request 门槛

PR 必须：

- 关联 issue，并说明所属版本及是否改变范围；
- 列出实际执行的测试、回放或真实环境验证与结果；
- 说明对数据口径、业务锁定项、隐私/凭证、schema 和通知行为的影响；
- 更新版本 README、长期事实、规则或 Changelog 中真正发生变化的部分；
- 通过仓库自动检查，并完成按风险等级所需的 review。

M0 现场证据、供应商授权、Windows 弹窗行为等无法由 CI 证明的事项，必须在 PR 中明确标为“人工验证”，不得用单元测试冒充。

## 版本与发布

- 预 1.0 阶段采用 `v0.x.0`；稳定版从 `v1.0.0` 开始。
- 版本范围与验收写入 `docs/visions/<version>/README.md`；用户可见或规则变化写入 `CHANGELOG.md`。
- 发布 tag 只能从 `main` 的已验证提交创建；有未归属验收欠账时不得封版。
- 完整流程见 `docs/process/release.md`。
