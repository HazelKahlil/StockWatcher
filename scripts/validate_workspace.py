"""Validate the governance and imported handoff baseline without dependencies."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "docs" / "reference" / "v2.0"

REQUIRED_PATHS = (
    ROOT / "AGENTS.md",
    ROOT / "CHANGELOG.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "docs" / "README.md",
    ROOT / "docs" / "project" / "index.md",
    ROOT / "docs" / "process" / "index.md",
    ROOT / "docs" / "process" / "boundaries.md",
    ROOT / "docs" / "process" / "dependencies.md",
    ROOT / "docs" / "visions" / "README.md",
    ROOT / "docs" / "visions" / "v0.1-mac-replay-foundation" / "README.md",
    ROOT / "docs" / "visions" / "v0.2-mac-local-alpha" / "README.md",
    ROOT / "docs" / "visions" / "v0.3-windows-data-gate" / "README.md",
    ROOT / "docs" / "visions" / "v0.4-v1-feature-complete" / "README.md",
    ROOT / "docs" / "visions" / "v0.5-stabilization" / "README.md",
    REFERENCE / "README.md",
    REFERENCE / "requirements.lock.json",
    REFERENCE / "SPEC_V2.0_AGENT.md",
    REFERENCE / "acceptance_tests.md",
    REFERENCE / "m0_checklist.md",
    REFERENCE / "config.example.yaml",
    REFERENCE / "schema.sql",
)

EXPECTED_IMAGES = tuple(
    REFERENCE / "assets" / "media" / f"image{number}.png" for number in range(1, 5)
)

MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")

RETIRED_VERSION_PATHS = (
    ROOT / "docs" / "visions" / "v0.1-m0-data-gate",
    ROOT / "docs" / "visions" / "v0.2-alpha-core",
    ROOT / "docs" / "visions" / "v0.3-v1-feature-complete",
    ROOT / "docs" / "visions" / "v0.4-stabilization",
)


def main() -> int:
    errors: list[str] = []

    for path in (*REQUIRED_PATHS, *EXPECTED_IMAGES):
        if not path.is_file():
            errors.append(f"missing required file: {path.relative_to(ROOT)}")

    for path in RETIRED_VERSION_PATHS:
        if path.exists():
            errors.append(
                f"retired pre-local-first version path still exists: {path.relative_to(ROOT)}"
            )

    lock_path = REFERENCE / "requirements.lock.json"
    if lock_path.is_file():
        try:
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"invalid requirements.lock.json: {exc}")
        else:
            if lock.get("version") != "2.0.0":
                errors.append("requirements.lock.json version must remain 2.0.0")
            product = lock.get("product", {})
            if product.get("auto_trading") is not False:
                errors.append("locked safety boundary auto_trading=false was changed")
            if product.get("reads_trading_passwords") is not False:
                errors.append("locked safety boundary reads_trading_passwords=false was changed")

    spec_path = REFERENCE / "SPEC_V2.0_AGENT.md"
    if spec_path.is_file():
        spec = spec_path.read_text(encoding="utf-8")
        if "/mnt/data/" in spec:
            errors.append("spec still contains runtime-local /mnt/data image links")
        for image in EXPECTED_IMAGES:
            relative = image.relative_to(REFERENCE).as_posix()
            if relative not in spec:
                errors.append(f"spec does not reference imported image: {relative}")

    for markdown_path in ROOT.rglob("*.md"):
        body = markdown_path.read_text(encoding="utf-8")
        for raw_target in MARKDOWN_LINK.findall(body):
            target = raw_target.strip().strip("<>")
            if not target or target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            target = target.split("#", 1)[0]
            resolved = (markdown_path.parent / target).resolve()
            if not resolved.exists():
                errors.append(
                    f"broken local Markdown link: {markdown_path.relative_to(ROOT)} -> {target}"
                )

    forbidden = tuple(ROOT.rglob("*.docx")) + tuple(ROOT.rglob("*.zip"))
    for path in forbidden:
        errors.append(
            f"duplicate binary handoff artifact must not be committed: {path.relative_to(ROOT)}"
        )

    if errors:
        print("Workspace validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Workspace validation passed.")
    print(f"Checked {len(REQUIRED_PATHS) + len(EXPECTED_IMAGES)} required files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
