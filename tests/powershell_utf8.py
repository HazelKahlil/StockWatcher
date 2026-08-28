from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

UTF8_CONSOLE_PREAMBLE = (
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
    "$OutputEncoding = [System.Text.Encoding]::UTF8"
)
_INVOKE_WRAPPER = Path(__file__).resolve().parent / "powershell" / "utf8_invoke.ps1"


def powershell_executable() -> str:
    candidates = [
        os.environ.get("STOCKWATCHER_PWSH"),
        shutil.which("pwsh"),
        os.path.join(
            os.environ.get("SystemRoot", r"C:\Windows"),
            "System32",
            "WindowsPowerShell",
            "v1.0",
            "powershell.exe",
        ),
        shutil.which("powershell"),
    ]
    for executable in candidates:
        if executable and Path(executable).is_file():
            return executable
    pytest.skip("PowerShell runtime is unavailable on this host")


def run_powershell_script(
    script: str | Path,
    *arguments: str,
    check: bool = True,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a PowerShell file after forcing UTF-8 console output.

    The wrapper is invoked with ``-File`` so Unicode arguments stay on argv
    instead of being re-encoded inside ``-Command``. Python still decodes
    stdout as UTF-8 and replaces leftover GBK bytes.
    """
    return subprocess.run(
        [
            powershell_executable(),
            "-NoLogo",
            "-NoProfile",
            "-File",
            str(_INVOKE_WRAPPER),
            str(Path(script).resolve()),
            *arguments,
        ],
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
