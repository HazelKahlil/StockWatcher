from __future__ import annotations

import codecs
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read_utf8_bom(path: Path, errors: list[str]) -> str:
    content = path.read_bytes()
    bom_count = content.count(codecs.BOM_UTF8)
    if not content.startswith(codecs.BOM_UTF8) or bom_count != 1:
        errors.append(
            f"{path.relative_to(ROOT)} must start with exactly one UTF-8 BOM"
        )
    payload = content[len(codecs.BOM_UTF8) :] if content.startswith(codecs.BOM_UTF8) else content
    try:
        return payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        errors.append(f"{path.relative_to(ROOT)} must be strict UTF-8 after its BOM: {error}")
        return ""


def _read_ascii(path: Path, errors: list[str]) -> str:
    try:
        return path.read_bytes().decode("ascii", errors="strict")
    except UnicodeDecodeError as error:
        errors.append(
            f"{path.relative_to(ROOT)} must be ASCII-safe for Windows Script Host: "
            f"{error}"
        )
        return ""


def main() -> int:
    required = (
        ROOT / "scripts" / "windows" / "stockwatcher.ps1",
        ROOT / "packaging" / "stockwatcher.spec",
        ROOT / "packaging" / "windows" / "version_info.txt",
        ROOT / "packaging" / "windows" / "StockWatcher.iss",
        ROOT / "packaging" / "windows" / "portable" / "启动 StockWatcher.vbs",
        ROOT / "packaging" / "windows" / "portable" / "stockwatcher_portable.py",
        ROOT / "packaging" / "windows" / "portable" / "第一次使用.md",
        ROOT / "packaging" / "windows" / "portable" / "DEPENDENCIES.md",
        ROOT / "scripts" / "build_internal_portable.py",
        ROOT / "src" / "stock_watcher" / "__main__.py",
        ROOT / "src" / "stock_watcher" / "providers" / "tdxquant_preflight.py",
        ROOT / "src" / "stock_watcher" / "ui" / "app.py",
    )
    errors = [f"missing {path.relative_to(ROOT)}" for path in required if not path.is_file()]
    if errors:
        print("\n".join(errors))
        return 1
    powershell = _read_utf8_bom(required[0], errors)
    for action in ("Setup", "Preflight", "Run", "Probe", "Build"):
        if f'"{action}"' not in powershell:
            errors.append(f"PowerShell entry is missing action {action}")
    if "127.0.0.1:17709" not in powershell:
        errors.append("PowerShell entry is missing the official TQ loopback endpoint")
    if 'return @("py", "-3.12")' in powershell:
        errors.append(
            "PowerShell entry must not force py -3.12 when another supported version exists"
        )
    if "Invoke-CheckedNative" not in powershell:
        errors.append("PowerShell entry must fail closed on native command errors")
    if "subst.exe" not in powershell or "Assert-IsccPathBudget" not in powershell:
        errors.append("PowerShell build must enforce the short ISCC input path contract")
    if '".swb"' not in powershell or '"h449-$stageId"' not in powershell:
        errors.append("PowerShell build must use an issue-owned staging directory")
    if "Publish-BuildArtifactsTransaction" not in powershell:
        errors.append("PowerShell build must publish installer and portable ZIP transactionally")
    if 'Destination = Join-Path $mappedRoot "dist\\' not in powershell:
        errors.append("PowerShell build must keep all publication paths on the short mapping")
    if "Write-FallbackPreflightReport" not in powershell:
        errors.append("PowerShell preflight must persist a fixed fail-closed fallback report")
    if "Read-ValidPreflightReport" not in powershell:
        errors.append("PowerShell preflight must validate the report before propagating failure")
    if "3.11 或 3.12" not in powershell:
        errors.append("PowerShell entry must declare the project-supported Python versions")
    if re.search(r"(?i)(token|password)\s*=", powershell):
        errors.append("PowerShell entry must not define credentials")
    portable_entry = _read_ascii(required[4], errors)
    portable_runtime = required[5].read_text(encoding="utf-8")
    if "pythonw.exe" not in portable_entry or "Get-Command pyw.exe" not in portable_entry:
        errors.append("portable entry must reuse the approved Python 3.12 Pythonw runtime")
    if "shell.Run(command, 0, True)" not in portable_entry:
        errors.append("portable entry must launch without a console window")
    if (
        "Get-AuthenticodeSignature" not in portable_entry
        or "Python Software Foundation" not in portable_entry
    ):
        errors.append("portable entry must verify the official Python signature")
    if "ExecutionPolicy" in portable_entry or "pip" in portable_entry.lower():
        errors.append("portable entry must not install dependencies or bypass execution policy")
    if "ChrW(&H672A)" not in portable_entry or "MsgBox failureMessage" not in portable_entry:
        errors.append("portable entry must construct its Chinese failure message safely")
    elevation_markers = (
        "runas",
        "-verb",
        "shellexecute",
        "requireadministrator",
        "highestavailable",
    )
    if any(marker in portable_entry.casefold() for marker in elevation_markers):
        errors.append("portable entry must not request elevation")
    if (
        "OFFICIAL_PUBLISHERS" not in portable_runtime
        or "Get-AuthenticodeSignature" not in portable_runtime
    ):
        errors.append(
            "portable runtime must verify the official terminal signature before preflight"
        )
    if (
        "subprocess.Popen" in portable_runtime
        or "attempt_start_official_terminal" in portable_runtime
    ):
        errors.append("portable runtime must not automatically start the official terminal")
    if "stock_watcher.providers.tdxquant_preflight" not in portable_runtime:
        errors.append("portable runtime must execute the packaged native preflight")
    if (
        "windows_live_verified" not in portable_runtime
        or 'getattr(check, "name", None) == "api_session"' not in portable_runtime
    ):
        errors.append("portable runtime must enforce the strict native preflight success contract")
    if "stock_watcher.ui.app" not in portable_runtime:
        errors.append("portable success path must launch the packaged StockWatcher UI")
    builder = required[8].read_text(encoding="utf-8")
    if (
        'ROOT / "src" / "stock_watcher"' not in builder
        or 'staging / "app" / "src" / "stock_watcher"' not in builder
    ):
        errors.append("portable ZIP must contain the complete stock_watcher application tree")
    installer = required[3].read_text(encoding="utf-8")
    if "PrivilegesRequired=lowest" not in installer:
        errors.append("installer must use per-user, non-admin installation")
    if "UninstallDelete" not in installer:
        errors.append("installer must declare uninstall behavior")
    if "StockWatcherBundleDir" not in installer or "StockWatcherOutputDir" not in installer:
        errors.append("installer must accept controlled short build paths")
    if errors:
        print("Windows package contract failed:")
        print("\n".join(f"- {error}" for error in errors))
        return 1
    print("Windows package contract passed (offline; no Windows/TdxQuant claim).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
