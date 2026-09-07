# Windows 0.7.0-alpha.3 UI synchronization

2026-09-07 - in progress - Owner: Codex; target-machine checks: Kahlil / Windows agent.

Base: PR #7 at b2a4f157f27a7e45d71126f7bd81e60c65d1500e.
UI reference: Mac/Web 5f64d0a9ea266196accfc0b9fea1c9fd973bb188.
This is a stacked PR targeting fix/windows-desktop-stability; neither PR is merged.

Port stable candidate controls and keyboard focus, asynchronous manual dialogs,
lazy history/review records, and background report/PDF work. Preserve PR #7's
Windows fonts, DPI layout, single instance, cooperative exit, provider timeout,
Credential Manager enforcement and late-result cancellation. No ranking, provider,
storage schema or scheduled-trigger changes. Same product version, platform-specific
source commits; Mac/Web deployments are unchanged.

Package identity must agree across Python, EXE resources, installer and CI ZIP.
Run offline tests/lint/types/package checks, then Windows Actions build. CI is not
physical Windows UI acceptance. PR #8's September 4 handoff remains applicable:
09:45 still needs a real trading-day check; do not upgrade during 09:25-15:35.

## Local verification — macOS arm64, 2026-09-07

- Full offline suite: 429 passed, 24 skipped, 2 live-provider tests deselected.
- Ruff and Mypy (120 files), workspace validator (29 required files), Windows package contract and diff check passed.
- New isolated Qt Replay probe: candidate identity through ten refreshes, Enter/Esc detail lifecycle, batched history completeness, and closing during blocked report reads. Existing close/settings/popup/history probes also passed.
- August 6 archived PDF integration tests now require `STOCKWATCHER_AUG06_FIXTURE`; they no longer implicitly consume today's personal Mac database, which has a different schema/history. Three archived-data tests were not run here; deterministic PDF tests passed.
- Windows callback factory retains WINFUNCTYPE/stdcall on Windows, with an unused CFUNCTYPE fallback for non-Windows offline imports.
- No provider/ranking/persistence production changes. Report tasks use daemon workers so closing Windows is not held by report I/O. Physical Windows visual smoothness is NOT_RUN.

## Target-machine handoff

Use [WINDOWS_TEST_PROMPT.md](WINDOWS_TEST_PROMPT.md). The Windows owner should verify the PR head, matching CI artifact provenance, installation, DPI/keyboard/close behavior, and live source-time progression. Preserve the separate 09:45 acceptance debt. CI run/results will be recorded on the PR; no merge or installed-Windows claim is made by this local handoff.
