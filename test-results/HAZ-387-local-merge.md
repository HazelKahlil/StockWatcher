# HAZ-387 本地合入与终态回读证据

环境：macOS（Mac 本地开发机），Mock/Replay；不代表 Windows、通达信或真实行情。

合入对象：Stage 7 `HAZ-390` 已 PASS 的 `0083e39bea124f8b854192cff34c7c579cf8a532`，快进合入本地 `main`。

合入后的本地 main 验证（2026-07-23）：

```text
uv sync --all-groups --frozen  PASS
uv lock --check                PASS
uv run pytest                  10 passed
uv run ruff check .            All checks passed!
uv run mypy src tests          Success: no issues found in 14 source files
python3 scripts/validate_workspace.py
                               Workspace validation passed. Checked 25 required files.
git diff --check               PASS
```

新的 detached 本地 worktree 从封版主干回读：v0.1 状态为“本地完成，待同步”，下一版本入口为 v0.2；在该 worktree 中重新执行 `uv sync --all-groups --frozen`、`uv run pytest`（10 passed）、workspace validation 和 `git diff --check`，均通过。

同步边界：不 push、不建 PR、不建 tag。封版提交后以 `git rev-list --left-right --count main...origin/main` 回读最终差异；GitHub 仍只包含此前里程碑镜像。
