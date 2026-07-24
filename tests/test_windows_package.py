from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace
from typing import cast

import pytest


def _function_body(powershell: str, name: str) -> str:
    function = re.search(
        rf"function {re.escape(name)}(?:\([^)]*\))?\s*\{{(?P<body>.*?)\n\}}",
        powershell,
        re.DOTALL,
    )
    assert function is not None
    return function.group("body")


def _powershell() -> str:
    executable = os.environ.get("STOCKWATCHER_PWSH") or shutil.which("pwsh")
    if executable is None:
        pytest.skip("PowerShell runtime is unavailable on this host")
    return executable


def _run_powershell_harness(*arguments: str) -> dict[str, object]:
    completed = subprocess.run(
        [
            _powershell(),
            "-NoLogo",
            "-NoProfile",
            "-File",
            "tests/powershell/stockwatcher_contract_harness.ps1",
            *arguments,
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return cast(dict[str, object], json.loads(completed.stdout.strip().splitlines()[-1]))


def _check(name: str, status: str, message: str, reason: str | None = None) -> dict[str, object]:
    return {"name": name, "status": status, "message": message, "reason": reason}


def _preflight_report(
    *,
    status: str = "PASS",
    tq_status: str = "PASS",
    api_status: str | None = "PASS",
    windows_live_verified: bool = True,
    include_extra_field: bool = False,
) -> dict[str, object]:
    checks = [
        _check("operating_system", "PASS", "Windows 环境已就绪。"),
        _check("python", "PASS", "Python 3.12.0（项目要求 3.11 或 3.12）。"),
        _check("terminal_install", "PASS", "已找到指定的官方终端目录。"),
        _check("python_client", "PASS", "已发现官方 tqcenter Python 客户端。"),
        _check(
            "tq_service",
            tq_status,
            (
                "TQ 本机端口可达。"
                if tq_status == "PASS"
                else "TQ 本机服务不可达，请确认终端支持 TQ，且 127.0.0.1:17709 已启动。"
            ),
            None if tq_status == "PASS" else "service_unreachable",
        ),
    ]
    if api_status is not None:
        checks.append(
            _check(
                "api_session",
                api_status,
                (
                    "官方股票列表接口可调用；这不代表字段、授权或性能 M0 已通过。"
                    if api_status == "PASS"
                    else "TdxQuant 返回了无法识别的数据，已安全停止候选输出。"
                ),
                None if api_status == "PASS" else "invalid_response",
            )
        )
    report: dict[str, object] = {
        "status": status,
        "platform": "Windows",
        "python_version": "3.12.0",
        "endpoint": "http://127.0.0.1:17709/",
        "checks": checks,
        "fund_module": "unavailable",
        "windows_live_verified": windows_live_verified,
    }
    if include_extra_field:
        report["unexpected"] = True
    return report


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_powershell_python_version_probe_is_valid_and_preserves_supported_range() -> None:
    powershell = Path("scripts/windows/stockwatcher.ps1").read_text(encoding="utf-8-sig")
    match = re.search(r'\s-c\s+"([^"]+)"', powershell)
    assert match is not None

    python_snippet = match.group(1)
    module = ast.parse(python_snippet, mode="exec")
    raise_statement = module.body[-1]
    assert isinstance(raise_statement, ast.Raise)
    assert isinstance(raise_statement.exc, ast.Call)
    exit_code = raise_statement.exc.args[0]
    assert isinstance(exit_code, ast.IfExp)

    version_predicate = compile(
        ast.Expression(exit_code.test),
        filename="stockwatcher.ps1:-c",
        mode="eval",
    )
    expected_support = {
        (3, 10): False,
        (3, 11): True,
        (3, 12): True,
        (3, 13): False,
    }
    for version, expected in expected_support.items():
        namespace = {"sys": SimpleNamespace(version_info=version)}
        assert bool(eval(version_predicate, {"__builtins__": {}}, namespace)) is expected


def test_powershell_preflight_preserves_arguments_and_native_failure_exit() -> None:
    powershell = Path("scripts/windows/stockwatcher.ps1").read_text(encoding="utf-8-sig")
    body = _function_body(powershell, "Invoke-Preflight")

    assert '$reportPath = Join-Path $ReportRoot "tdxquant-preflight.json"' in body
    assert '"--output", $attemptPath' in body
    assert '$arguments += @("--terminal-path", $TdxInstallPath)' in body
    assert "Invoke-PreflightProcess -Arguments $arguments" in body
    assert "Write-FallbackPreflightReport -Path $attemptPath" in body
    assert "Publish-PreflightReport -AttemptPath $attemptPath -ReportPath $reportPath" in body
    assert "if ($exitCode -ne 0)" in body
    assert "Remove-Item -LiteralPath $attemptPath -Force" in body
    assert re.search(r"catch \{.*?exit 1\s*\}", powershell, re.DOTALL)


def test_powershell_preflight_validates_missing_malformed_and_unsafe_reports() -> None:
    powershell = Path("scripts/windows/stockwatcher.ps1").read_text(encoding="utf-8-sig")
    validator = _function_body(powershell, "Read-ValidPreflightReport")

    assert "Test-Path -LiteralPath $Path -PathType Leaf" in validator
    assert "System.Text.UTF8Encoding" in validator
    assert "ConvertFrom-Json" in validator
    assert "预检报告包含禁止字段" in validator
    assert '"api_session"' in validator
    assert "$expectedLive" in validator
    assert "$report.windows_live_verified -ne $expectedLive" in validator
    assert "$report.status -ne $derivedStatus" in validator
    assert "预检报告检查集合或顺序非法" in validator
    fallback = _function_body(powershell, "Write-FallbackPreflightReport")
    assert 'status = "FAIL"' in fallback
    assert 'name = "api_session"' in fallback
    assert 'status = "PASS"' not in fallback
    assert "windows_live_verified = $false" in fallback


def test_build_uses_short_owned_stage_with_conservative_iscc_budget() -> None:
    powershell = Path("scripts/windows/stockwatcher.ps1").read_text(encoding="utf-8-sig")
    body = _function_body(powershell, "Invoke-Build")

    assert 'Invoke-CheckedNative -Command "subst.exe"' in body
    assert '$stageParent = Join-Path $mappedRoot ".swb"' in body
    assert '$stageRoot = Join-Path $stageParent "h449-$stageId"' in body
    assert "--distpath" in body and "--workpath" in body
    assert "Assert-IsccPathBudget" in body
    assert "/DStockWatcherBundleDir=$bundleRoot" in body
    assert "/DStockWatcherOutputDir=$stageInstaller" in body
    assert "Publish-BuildArtifactsTransaction" in body
    assert 'Destination = Join-Path $mappedRoot "dist\\installer' in body
    assert 'Destination = Join-Path $mappedRoot "dist\\StockWatcher' in body

    relative_generated_path = PureWindowsPath(
        "_internal",
        "PySide6",
        "translations",
        "qtwebengine_locales",
        "stockwatcher-generated-" + ("x" * 88) + ".pak",
    )
    deep_checkout = PureWindowsPath(
        "C:/Users",
        "用户名 with spaces " + ("u" * 60),
        "worktrees",
        "深层 checkout " + ("d" * 65),
    )
    original = deep_checkout / "dist" / "StockWatcher" / relative_generated_path
    short = (
        PureWindowsPath("Z:/.swb/h449-123456789abc/d/StockWatcher")
        / relative_generated_path
    )
    assert len(str(original)) > 260
    assert len(str(short)) <= 240


def test_build_cleanup_is_scoped_and_failure_preserves_existing_artifacts() -> None:
    powershell = Path("scripts/windows/stockwatcher.ps1").read_text(encoding="utf-8-sig")
    body = _function_body(powershell, "Invoke-Build")

    assert '$runId = [Guid]::NewGuid().ToString("N")' in body
    assert '(Split-Path -Parent $stageRoot) -eq $expectedParent' in body
    assert '(Split-Path -Leaf $stageRoot) -eq "h449-$stageId"' in body
    assert "Remove-Item -LiteralPath $stageRoot -Recurse -Force" in body
    assert "Remove-Item -LiteralPath $ProjectRoot" not in body
    assert "Remove-Item -LiteralPath (Join-Path $ProjectRoot" not in body
    assert body.index("Compress-Archive") < body.index("Publish-BuildArtifactsTransaction")
    assert body.index("Publish-BuildArtifactsTransaction") < body.index("finally")


@pytest.mark.parametrize(
    ("fixture_kind", "child_exit", "expected_status", "expected_throw", "fallback"),
    [
        ("pass", 0, "PASS", False, False),
        ("fail", 2, "FAIL", True, False),
        ("fail", 0, "FAIL", True, False),
        ("warn", 2, "WARN", True, False),
        ("pass", 9, "FAIL", True, True),
        ("semantic_conflict", 0, "FAIL", True, True),
        ("incomplete", 0, "FAIL", True, True),
        ("api_only_pass", 0, "FAIL", True, True),
        ("duplicate_check", 0, "FAIL", True, True),
        ("unknown_check", 0, "FAIL", True, True),
        ("missing_check", 0, "FAIL", True, True),
        ("pass_live_false", 0, "FAIL", True, True),
        ("fail_live_true", 2, "FAIL", True, True),
        ("schema_error", 0, "FAIL", True, True),
        ("invalid_json", 0, "FAIL", True, True),
        ("invalid_utf8", 0, "FAIL", True, True),
        ("missing", 0, "FAIL", True, True),
        ("start_failure", 0, "FAIL", True, True),
    ],
)
def test_powershell_preflight_executes_fail_closed_behavior_matrix(
    tmp_path: Path,
    fixture_kind: str,
    child_exit: int,
    expected_status: str,
    expected_throw: bool,
    fallback: bool,
) -> None:
    runtime_root = tmp_path / "运行 根 with spaces"
    terminal_path = runtime_root / "官方 终端目录"
    terminal_path.mkdir(parents=True)
    fixture = tmp_path / "fixture.json"
    fixture_argument = str(fixture)
    if fixture_kind == "pass":
        _write_json(fixture, _preflight_report())
    elif fixture_kind == "fail":
        _write_json(
            fixture,
            _preflight_report(
                status="FAIL",
                tq_status="FAIL",
                api_status="FAIL",
                windows_live_verified=False,
            ),
        )
    elif fixture_kind == "warn":
        payload = _preflight_report(windows_live_verified=False)
        checks = cast(list[dict[str, object]], payload["checks"])
        checks[3] = _check(
            "python_client",
            "WARN",
            "未发现 tqcenter；可继续使用官方 127.0.0.1:17709 HTTP 模式。",
            "dependency_missing",
        )
        payload["status"] = "WARN"
        _write_json(fixture, payload)
    elif fixture_kind == "semantic_conflict":
        _write_json(
            fixture,
            _preflight_report(status="PASS", tq_status="FAIL", api_status="PASS"),
        )
    elif fixture_kind == "incomplete":
        _write_json(
            fixture,
            _preflight_report(api_status=None, windows_live_verified=True),
        )
    elif fixture_kind == "api_only_pass":
        payload = _preflight_report()
        payload["checks"] = [
            _check(
                "api_session",
                "PASS",
                "官方股票列表接口可调用；这不代表字段、授权或性能 M0 已通过。",
            )
        ]
        _write_json(fixture, payload)
    elif fixture_kind == "duplicate_check":
        payload = _preflight_report()
        checks = cast(list[dict[str, object]], payload["checks"])
        checks.append(
            _check(
                "api_session",
                "PASS",
                "官方股票列表接口可调用；这不代表字段、授权或性能 M0 已通过。",
            )
        )
        _write_json(fixture, payload)
    elif fixture_kind == "unknown_check":
        payload = _preflight_report()
        checks = cast(list[dict[str, object]], payload["checks"])
        checks[0] = _check("unknown_check", "PASS", "Windows 环境已就绪。")
        _write_json(fixture, payload)
    elif fixture_kind == "missing_check":
        payload = _preflight_report()
        checks = cast(list[dict[str, object]], payload["checks"])
        del checks[2]
        _write_json(fixture, payload)
    elif fixture_kind == "pass_live_false":
        _write_json(
            fixture,
            _preflight_report(windows_live_verified=False),
        )
    elif fixture_kind == "fail_live_true":
        _write_json(
            fixture,
            _preflight_report(
                status="FAIL",
                tq_status="FAIL",
                api_status="FAIL",
                windows_live_verified=True,
            ),
        )
    elif fixture_kind == "schema_error":
        _write_json(fixture, _preflight_report(include_extra_field=True))
    elif fixture_kind == "invalid_json":
        fixture.write_text("{not json", encoding="utf-8")
    elif fixture_kind == "invalid_utf8":
        fixture.write_bytes(b'{"status":"PASS","bad":"\\xff"}' + b"\xff")
    elif fixture_kind == "missing":
        fixture_argument = "MISSING"
    elif fixture_kind == "start_failure":
        fixture_argument = "START_FAILURE"
    else:
        raise AssertionError(f"unknown fixture kind {fixture_kind}")

    result = _run_powershell_harness(
        "-Mode",
        "Preflight",
        "-WorkRoot",
        str(runtime_root),
        "-FixturePath",
        fixture_argument,
        "-ChildExitCode",
        str(child_exit),
        "-ExpectedTerminalPath",
        str(terminal_path),
    )

    assert result["arguments_preserved"] is True
    assert result["report_exists"] is True
    assert result["temporary_count"] == 0
    assert result["threw"] is expected_throw, result
    report = result["report"]
    assert isinstance(report, dict)
    assert report["status"] == expected_status
    if fallback:
        assert report["checks"] == [
            {
                "name": "api_session",
                "status": "FAIL",
                "message": "TdxQuant 返回了无法识别的数据，已安全停止候选输出。",
                "reason": "invalid_response",
            }
        ]


@pytest.mark.parametrize(
    ("mode", "expected_installer", "expected_portable", "expected_throw"),
    [
        ("PublishSuccess", "new-installer", "new-portable", False),
        ("PublishFailure", "old-installer", "old-portable", True),
    ],
)
def test_powershell_build_publish_transaction_executes_on_deep_unicode_tree(
    tmp_path: Path,
    mode: str,
    expected_installer: str,
    expected_portable: str,
    expected_throw: bool,
) -> None:
    deep_root = tmp_path
    while len(str(deep_root)) <= 285:
        deep_root /= "深层 目录 with spaces repeated"
    deep_root.mkdir(parents=True)
    short_root = tmp_path / "短映射"
    short_root.symlink_to(deep_root, target_is_directory=True)
    source_root = short_root / ".swb" / "source"
    source_root.mkdir(parents=True)
    (source_root / "StockWatcher-setup.exe").write_text("new-installer", encoding="utf-8")
    (source_root / "StockWatcher-portable.zip").write_text("new-portable", encoding="utf-8")
    installer = deep_root / "dist" / "installer" / "StockWatcher-setup.exe"
    portable = deep_root / "dist" / "StockWatcher-portable.zip"
    installer.parent.mkdir(parents=True)
    installer.write_text("old-installer", encoding="utf-8")
    portable.write_text("old-portable", encoding="utf-8")

    result = _run_powershell_harness(
        "-Mode",
        mode,
        "-WorkRoot",
        str(tmp_path / "runtime"),
        "-ShortRoot",
        str(short_root),
    )

    assert len(str(installer)) > 260
    assert result == {
        "threw": expected_throw,
        "installer": expected_installer,
        "portable": expected_portable,
        "transaction_count": 0,
    }
    if mode == "PublishSuccess":
        (source_root / "StockWatcher-setup.exe").write_text(
            "newer-installer", encoding="utf-8"
        )
        (source_root / "StockWatcher-portable.zip").write_text(
            "newer-portable", encoding="utf-8"
        )
        repeated = _run_powershell_harness(
            "-Mode",
            mode,
            "-WorkRoot",
            str(tmp_path / "runtime"),
            "-ShortRoot",
            str(short_root),
        )
        assert repeated == {
            "threw": False,
            "installer": "newer-installer",
            "portable": "newer-portable",
            "transaction_count": 0,
        }


def test_windows_matrix_requires_parseable_report_after_preflight_failure() -> None:
    workflow = Path(".github/workflows/governance.yml").read_text(encoding="utf-8")

    assert 'Join-Path $env:RUNNER_TEMP "官方 终端目录"' in workflow
    assert "-TdxInstallPath $terminalPath" in workflow
    report_join = 'Join-Path $env:LOCALAPPDATA "StockWatcher\\reports\\tdxquant-preflight.json"'
    assert report_join in workflow
    assert "if (-not (Test-Path $reportPath))" in workflow
    assert "Get-Content -Raw -Encoding UTF8 $reportPath | ConvertFrom-Json" in workflow
    assert 'if ($report.status -ne "FAIL")' in workflow
    assert "dist/StockWatcher-0.3.0-alpha-portable.zip" in workflow
    assert "dist/StockWatcher/**" not in workflow
