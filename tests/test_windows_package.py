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
