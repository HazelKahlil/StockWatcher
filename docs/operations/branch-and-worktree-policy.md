# Branch 与 worktree policy

## 长期目标

- `main`：唯一共享核心与 Desktop App Mac 基线。
- `web/internal-test-v1`：Web 未验收前保留；当前 worktree 位于 `90-Archive/StockWatcher/00-current/web/`，不合入 main。
- `windows/internal-test-v1`：真正开始 Windows 时才创建。
- `import/rc4-strict-audit`：历史分支暂保留；其 patch-id 与 main 已消费的 RC4 修复等价，但 commit 不是 main ancestry，禁止 force 删除，禁止重复 cherry-pick。

## Worktree

- 20-Projects 只保留 `StockWatcher` 主 worktree。
- Web 未验收线最多保留一个独立 worktree；当前位于归档 current 区，源码、Bundle、Handoff 一致保存。
- Windows 开工时再建临时 worktree；验收后正常移除。
- 已完成/废弃 worktree 必须先确认 clean、无未跟踪源码，再使用普通 `git worktree remove`；本轮未使用 `--force`。
- 收口后执行 `git worktree prune`，只清理已不存在的 worktree 元数据。

## 分支删除门

1. 运行 `git branch --merged main` 并逐分支确认。
2. 检查 `git log --oneline main..<branch>`、工作树、stash 和未跟踪文件。
3. 先保存 Bundle/patch/manifest。
4. 只能使用 `git branch -d`；`-D` 禁止。
5. 有独有内容、无法判断价值或仍被 worktree 使用时保留并标记。
