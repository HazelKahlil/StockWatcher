# HAZ-384 verification evidence

Environment: macOS arm64; data source: deterministic Mock/Replay/Synthetic only.

Executed on 2026-07-22:

```text
uv sync --all-groups                         PASS
uv run pytest                                PASS (5 passed)
uv run ruff check .                          PASS (All checks passed!)
uv run mypy src tests                        PASS (no issues in 13 source files)
python3 scripts/validate_workspace.py        PASS (24 required files)
git diff --check                             PASS
```

This is not evidence for Windows, 通达信, supplier authorization, purple/yellow fund lines,
real-time market data, Windows notifications, tray/multi-monitor behavior, or packaging.
