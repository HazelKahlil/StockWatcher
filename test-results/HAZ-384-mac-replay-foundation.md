# HAZ-384 / HAZ-388 verification evidence

Environment: macOS arm64; data source: deterministic Mock/Replay/Synthetic only.

Executed on 2026-07-22:

```text
uv sync --all-groups --frozen                PASS
uv lock --check                              PASS
uv run pytest                                PASS (8 passed)
uv run ruff check .                          PASS (All checks passed!)
uv run mypy src tests                        PASS (no issues in 14 source files)
python3 scripts/validate_workspace.py        PASS (25 required files)
git diff --check                             PASS
```

HAZ-388 regression coverage: STOPPED survives duplicate `code + source_ts`; its source/received
timestamps and provider/config versions remain available; recovery requires three distinct WARMING
samples before a newer HEALTHY sample is candidate-safe; non-Asia/Shanghai timestamps are rejected;
logging redacts token/account values and rolls files.

This is not evidence for Windows, 通达信, supplier authorization, purple/yellow fund lines,
real-time market data, Windows notifications, tray/multi-monitor behavior, or packaging.
