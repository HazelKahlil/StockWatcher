from __future__ import annotations

import ast
import re
from pathlib import Path
from types import SimpleNamespace


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
    function = re.search(
        r"function Invoke-Preflight \{(?P<body>.*?)\n\}", powershell, re.DOTALL
    )
    assert function is not None
    body = function.group("body")

    assert '"--output", (Join-Path $ReportRoot "tdxquant-preflight.json")' in body
    assert '$arguments += @("--terminal-path", $TdxInstallPath)' in body
    assert (
        "Invoke-CheckedNative -Command $PythonPath -Arguments $arguments "
        '-FailureMessage "预检未通过'
    ) in body
    assert re.search(
        r'if \(\$exitCode -ne 0\) \{\s*throw "\$FailureMessage', powershell
    )
    assert re.search(r"catch \{.*?exit 1\s*\}", powershell, re.DOTALL)


def test_windows_matrix_requires_parseable_report_after_preflight_failure() -> None:
    workflow = Path(".github/workflows/governance.yml").read_text(encoding="utf-8")

    assert 'Join-Path $env:RUNNER_TEMP "官方 终端目录"' in workflow
    assert "-TdxInstallPath $terminalPath" in workflow
    report_join = 'Join-Path $env:LOCALAPPDATA "StockWatcher\\reports\\tdxquant-preflight.json"'
    assert report_join in workflow
    assert "if (-not (Test-Path $reportPath))" in workflow
    assert "Get-Content -Raw -Encoding UTF8 $reportPath | ConvertFrom-Json" in workflow
    assert 'if ($report.status -ne "FAIL")' in workflow
