from __future__ import annotations

import hashlib
import importlib.util
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT / "packaging" / "windows" / "portable" / "stockwatcher_portable.py"
)


def _load_path(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_module() -> ModuleType:
    return _load_path("stockwatcher_portable", MODULE_PATH)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def test_unique_entry_is_hidden_signed_python_312_only() -> None:
    entry = (
        ROOT / "packaging" / "windows" / "portable" / "启动 StockWatcher.vbs"
    )
    source = entry.read_bytes().decode("ascii")
    assert "pythonw.exe" in source
    assert "Get-Command pyw.exe" in source
    assert "'-3.12'" in source
    assert "Get-AuthenticodeSignature" in source
    assert "Python Software Foundation" in source
    assert "shell.Run(command, 0, True)" in source
    assert "ExecutionPolicy" not in source
    assert "pip" not in source.lower()
    assert "http" not in source.lower()
    encoded_text = "".join(
        chr(int(codepoint, 16))
        for codepoint in re.findall(r"ChrW\(&H([0-9A-F]{4})\)", source)
    )
    assert encoded_text == "未找到数字签名有效、发布者匹配的。未启动。"
    assert '" Python Software Foundation "' in source
    assert '" Python 3.12 Pythonw"' in source
    assert '"StockWatcher "' in source


def test_vbs_launcher_has_no_elevation_verb() -> None:
    source = (
        ROOT / "packaging" / "windows" / "portable" / "启动 StockWatcher.vbs"
    ).read_text(encoding="utf-8")
    forbidden = (
        "runas",
        "-verb",
        "shellexecute",
        "requireadministrator",
        "highestavailable",
    )
    assert all(item not in source.casefold() for item in forbidden)


def test_portable_runtime_contains_no_install_or_security_bypass() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    forbidden = (
        "pip install",
        "ExecutionPolicy Bypass",
        "Set-MpPreference",
        "New-SelfSignedCertificate",
        "trustedpeople",
        "http://0.0.0.0",
    )
    assert all(item.casefold() not in source.casefold() for item in forbidden)
    assert "127.0.0.1:17709" not in source
    assert "_system_executable" in source
    assert "run_native_preflight" in source
    assert "launch_stockwatcher_ui" in source
    assert "subprocess.Popen" not in source
    assert "attempt_start_official_terminal" not in source


def test_missing_application_or_native_preflight_is_rejected(tmp_path: Path) -> None:
    module = _load_module()
    script = tmp_path / "完整 包" / "portable" / "stockwatcher_portable.py"
    script.parent.mkdir(parents=True)
    script.write_text("# fixture", encoding="utf-8")
    layout = module.portable_layout(script)

    with pytest.raises(module.PortableLaunchError, match="缺少 StockWatcher 应用"):
        module.validate_application(layout)

    package = layout.application_src / "stock_watcher"
    (package / "providers").mkdir(parents=True)
    (package / "ui").mkdir(parents=True)
    for path in (
        package / "__init__.py",
        package / "__main__.py",
        package / "ui" / "app.py",
        layout.project_metadata,
        layout.dependency_lock,
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    with pytest.raises(module.PortableLaunchError, match="原生预检"):
        module.validate_application(layout)


def test_dependency_check_reports_exact_runtime_prerequisites() -> None:
    module = _load_module()
    missing = module.missing_dependencies(
        lambda name: None if name in {"PySide6", "yaml"} else object(),
        lambda _distribution: {
            "pydantic": "2.13.4",
            "tzdata": "2026.3",
        }.get(_distribution, "fixture"),
    )
    assert missing == (
        "PySide6 6.11.1",
        "PyYAML 6.0.3",
    )
    message = module._dependency_message(missing)
    assert "不会联网安装依赖" in message
    assert "PySide6 6.11.1" in message
    assert "PyYAML 6.0.3" in message


def test_only_strict_native_preflight_pass_allows_ui() -> None:
    module = _load_module()
    pass_status = SimpleNamespace(PASS="PASS")

    valid = SimpleNamespace(
        status="PASS",
        windows_live_verified=True,
        checks=(SimpleNamespace(name="api_session", status="PASS"),),
    )
    assert module._strict_preflight_pass(valid, pass_status) is True

    invalid_reports = (
        SimpleNamespace(
            status="PASS",
            windows_live_verified=False,
            checks=(SimpleNamespace(name="api_session", status="PASS"),),
        ),
        SimpleNamespace(
            status="PASS",
            windows_live_verified=True,
            checks=(
                SimpleNamespace(name="api_session", status="PASS"),
                SimpleNamespace(name="api_session", status="PASS"),
            ),
        ),
        SimpleNamespace(
            status="PASS",
            windows_live_verified=True,
            checks=(SimpleNamespace(name="api_session", status="FAIL"),),
        ),
        SimpleNamespace(status="PASS", windows_live_verified=True, checks=()),
    )
    assert all(
        module._strict_preflight_pass(report, pass_status) is False
        for report in invalid_reports
    )


def test_success_path_calls_real_ui_only_after_native_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module()
    calls: list[str] = []
    terminal = ROOT / "fixture-TdxW.exe"
    monkeypatch.setattr(module, "validate_application", lambda _layout: calls.append("layout"))
    monkeypatch.setattr(module, "missing_dependencies", lambda: ())
    monkeypatch.setattr(module, "find_official_terminal", lambda: terminal)

    def preflight(_layout: object, *, terminal: Path) -> bool:
        calls.append("preflight")
        return True

    def ui(_layout: object) -> int:
        calls.append("ui")
        return 0

    monkeypatch.setattr(module, "run_native_preflight", preflight)
    monkeypatch.setattr(module, "launch_stockwatcher_ui", ui)

    assert module.launch_once(module.portable_layout()) == 0
    assert calls == ["layout", "preflight", "ui"]


def test_preflight_failure_does_not_start_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module()
    calls: list[str] = []
    monkeypatch.setattr(module, "validate_application", lambda _layout: None)
    monkeypatch.setattr(module, "missing_dependencies", lambda: ())
    monkeypatch.setattr(module, "find_official_terminal", lambda: ROOT / "TdxW.exe")
    monkeypatch.setattr(module, "run_native_preflight", lambda *_args, **_kwargs: False)

    def start_terminal(*_args: object, **_kwargs: object) -> object:
        calls.append("terminal")
        return object()

    def ui(_layout: object) -> int:
        calls.append("ui")
        return 0

    monkeypatch.setattr(module.subprocess, "Popen", start_terminal)
    monkeypatch.setattr(module, "launch_stockwatcher_ui", ui)

    with pytest.raises(module.PortableLaunchError, match="原生 TdxQuant 预检未通过"):
        module.launch_once(module.portable_layout())
    assert calls == []


def test_frozen_bundle_skips_external_python_and_source_layout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module()
    calls: list[str] = []
    terminal = ROOT / "fixture-TdxW.exe"
    monkeypatch.setattr(module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        module,
        "validate_application",
        lambda _layout: pytest.fail("frozen bundle must not require source layout"),
    )
    monkeypatch.setattr(
        module,
        "missing_dependencies",
        lambda: pytest.fail("frozen bundle must not require external Python packages"),
    )
    monkeypatch.setattr(module, "find_official_terminal", lambda: terminal)

    def preflight(layout: object, *, terminal: Path) -> bool:
        assert layout is None
        calls.append("preflight")
        return True

    def ui(layout: object) -> int:
        assert layout is None
        calls.append("ui")
        return 0

    monkeypatch.setattr(module, "run_native_preflight", preflight)
    monkeypatch.setattr(module, "launch_stockwatcher_ui", ui)

    assert module.launch_once() == 0
    assert calls == ["preflight", "ui"]


def test_pyinstaller_bundle_uses_strict_windows_entry() -> None:
    source = (ROOT / "packaging" / "stockwatcher.spec").read_text(encoding="utf-8")
    assert "stockwatcher_portable.py" in source
    assert 'src" / "stock_watcher" / "__main__.py' not in source
    assert '"stock_watcher.ui.app"' in source


def test_build_portable_zip_contains_complete_app_and_verified_manifest(
    tmp_path: Path,
) -> None:
    builder = _load_path(
        "build_internal_portable", ROOT / "scripts" / "build_internal_portable.py"
    )
    output = tmp_path / "中文 目录 with spaces" / "StockWatcher-Internal-Portable.zip"
    path, digest, count = builder.build(output)
    assert path == output
    assert digest == _sha256(path)

    extract_root = tmp_path / "全新 解包"
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        assert len(names) == count
        required = {
            "StockWatcher-Internal-Portable/启动 StockWatcher.vbs",
            "StockWatcher-Internal-Portable/portable/stockwatcher_portable.py",
            "StockWatcher-Internal-Portable/app/pyproject.toml",
            "StockWatcher-Internal-Portable/app/uv.lock",
            "StockWatcher-Internal-Portable/app/src/stock_watcher/__main__.py",
            (
                "StockWatcher-Internal-Portable/app/src/stock_watcher/"
                "providers/tdxquant_preflight.py"
            ),
            "StockWatcher-Internal-Portable/app/src/stock_watcher/ui/app.py",
            "StockWatcher-Internal-Portable/MANIFEST.sha256",
        }
        assert required <= names
        archive.extractall(extract_root)

    package_root = extract_root / "StockWatcher-Internal-Portable"
    manifest_lines = (package_root / "MANIFEST.sha256").read_text(
        encoding="utf-8"
    ).splitlines()
    manifest_paths: set[str] = set()
    for line in manifest_lines:
        expected, relative = line.split("  ", 1)
        target = package_root / relative
        assert target.is_file()
        assert _sha256(target) == expected
        manifest_paths.add(relative)
    delivered_payload = {
        path.relative_to(package_root).as_posix()
        for path in package_root.rglob("*")
        if path.is_file() and path.name != "MANIFEST.sha256"
    }
    assert manifest_paths == delivered_payload

    app_src = package_root / "app" / "src"
    code = (
        "import pathlib,sys;"
        f"root=pathlib.Path({str(app_src)!r}).resolve();"
        "sys.path.insert(0,str(root));"
        "import stock_watcher;"
        "from stock_watcher.providers import tdxquant_preflight;"
        "from stock_watcher.ui import app;"
        "assert pathlib.Path(stock_watcher.__file__).resolve().is_relative_to(root);"
        "assert callable(tdxquant_preflight.run_preflight);"
        "assert callable(app.run)"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
