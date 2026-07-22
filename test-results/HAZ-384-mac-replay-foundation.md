# HAZ-384 / HAZ-388 / HAZ-391 verification evidence

Environment: macOS arm64; data source: deterministic Mock/Replay/Synthetic only.

Executed on 2026-07-23:

```text
uv sync --all-groups --frozen                PASS
uv lock --check                              PASS
uv run pytest                                PASS (10 passed)
uv run ruff check .                          PASS (All checks passed!)
uv run mypy src tests                        PASS (no issues in 14 source files)
python3 scripts/validate_workspace.py        BLOCKED (pre-existing AGENTS.md runtime links)
git diff --check                             PASS
```

HAZ-388/HAZ-391 regression coverage: STOPPED survives duplicate `code + source_ts`; its
source/received timestamps and provider/config versions remain available; its `source_ts` becomes
the persisted recovery cutoff. Delayed WARMING/HEALTHY samples at or before that cutoff are rejected
and counted, even when received later. Recovery requires three distinct WARMING samples and a newer
HEALTHY sample, all strictly later than the cutoff, before a candidate is safe. Non-Asia/Shanghai
timestamps are rejected; logging redacts token/account values and rolls files.

The workspace validator was run but currently treats the pre-existing platform-injected `mention://`
links and an example local path in `AGENTS.md` as broken local Markdown links. HAZ-391 does not
modify `AGENTS.md`; all HAZ-391 source and test checks above passed.

This is not evidence for Windows, 通达信, supplier authorization, purple/yellow fund lines,
real-time market data, Windows notifications, tray/multi-monitor behavior, or packaging.
