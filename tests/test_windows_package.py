from __future__ import annotations

import ast
import re
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace


def _function_body(powershell: str, name: str) -> str:
    function = re.search(
        rf"function {re.escape(name)}(?:\([^)]*\))?\s*\{{(?P<body>.*?)\n\}}",
        powershell,
        re.DOTALL,
    )
    assert function is not None
    return function.group("body")


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
    assert "& $PythonPath @arguments" in body
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
    assert "$apiPasses.Count -ne 1" in validator
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
    assert '$stageRoot = Join-Path $stageParent "h447-$stageId"' in body
    assert "--distpath" in body and "--workpath" in body
    assert "Assert-IsccPathBudget" in body
    assert "/DStockWatcherBundleDir=$bundleRoot" in body
    assert "/DStockWatcherOutputDir=$stageInstaller" in body
    assert "Publish-BuildArtifact -Source $installer" in body
    assert "Publish-BuildArtifact -Source $portable" in body

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
        PureWindowsPath("Z:/.swb/h447-123456789abc/d/StockWatcher")
        / relative_generated_path
    )
    assert len(str(original)) > 260
    assert len(str(short)) <= 240


def test_build_cleanup_is_scoped_and_failure_preserves_existing_artifacts() -> None:
    powershell = Path("scripts/windows/stockwatcher.ps1").read_text(encoding="utf-8-sig")
    body = _function_body(powershell, "Invoke-Build")

    assert '$runId = [Guid]::NewGuid().ToString("N")' in body
    assert '(Split-Path -Parent $stageRoot) -eq $expectedParent' in body
    assert '(Split-Path -Leaf $stageRoot) -eq "h447-$stageId"' in body
    assert "Remove-Item -LiteralPath $stageRoot -Recurse -Force" in body
    assert "Remove-Item -LiteralPath $ProjectRoot" not in body
    assert "Remove-Item -LiteralPath (Join-Path $ProjectRoot" not in body
    assert body.index("Compress-Archive") < body.index("Publish-BuildArtifact")
    assert body.index("Publish-BuildArtifact") < body.index("finally")


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
