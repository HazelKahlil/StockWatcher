from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    required = (
        ROOT / "scripts" / "windows" / "stockwatcher.ps1",
        ROOT / "packaging" / "stockwatcher.spec",
        ROOT / "packaging" / "windows" / "version_info.txt",
        ROOT / "packaging" / "windows" / "StockWatcher.iss",
    )
    errors = [f"missing {path.relative_to(ROOT)}" for path in required if not path.is_file()]
    if errors:
        print("\n".join(errors))
        return 1
    powershell = required[0].read_text(encoding="utf-8")
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
    if "3.11 或 3.12" not in powershell:
        errors.append("PowerShell entry must declare the project-supported Python versions")
    if re.search(r"(?i)(token|password)\s*=", powershell):
        errors.append("PowerShell entry must not define credentials")
    installer = required[3].read_text(encoding="utf-8")
    if "PrivilegesRequired=lowest" not in installer:
        errors.append("installer must use per-user, non-admin installation")
    if "UninstallDelete" not in installer:
        errors.append("installer must declare uninstall behavior")
    if errors:
        print("Windows package contract failed:")
        print("\n".join(f"- {error}" for error in errors))
        return 1
    print("Windows package contract passed (offline; no Windows/TdxQuant claim).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
