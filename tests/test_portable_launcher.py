from __future__ import annotations

import importlib.util
import io
import json
import sys
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any

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


class _Response:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_portable_probe_uses_exact_read_only_contract() -> None:
    module = _load_module()
    captured: dict[str, Any] = {}

    def opener(request: object, *, timeout: float) -> _Response:
        captured["request"] = request
        captured["timeout"] = timeout
        return _Response(
            {"result": {"ErrorId": 0, "Value": ["000001.SZ", "600000.SH"]}}
        )

    result = module.probe_tq(opener=opener)

    assert result.connected is True
    request = captured["request"]
    assert request.full_url == "http://127.0.0.1:17709/"
    assert request.method == "POST"
    assert json.loads(request.data) == {
        "id": 1,
        "method": "get_stock_list",
        "params": {"market": "5", "list_type": 0},
    }


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"result": {"ErrorId": 10}}, "api_rejected"),
        ({"result": {"ErrorId": 0, "Value": []}}, "api_rejected"),
        ({"result": {"ErrorId": 0, "Value": {"unexpected": 1}}}, "api_rejected"),
        ({"bad": "shape"}, "api_rejected"),
    ],
)
def test_portable_probe_fails_closed(payload: object, expected: str) -> None:
    module = _load_module()
    result = module.probe_tq(opener=lambda *_args, **_kwargs: _Response(payload))
    assert result.state == expected
    assert result.connected is False


def test_portable_probe_classifies_unreachable_without_details() -> None:
    module = _load_module()

    def unavailable(*_args: object, **_kwargs: object) -> object:
        raise OSError("account=alice path=C:\\Users\\alice body=secret")

    result = module.probe_tq(opener=unavailable)
    assert result.state == "service_unavailable"
    assert "alice" not in module.STATE_TEXT[result.state][1]
    assert "secret" not in module.STATE_TEXT[result.state][1]


def test_unique_entry_is_hidden_and_does_not_bypass_policy() -> None:
    source = (
        ROOT / "packaging" / "windows" / "portable" / "启动 StockWatcher.vbs"
    ).read_text(encoding="utf-8")
    assert "pythonw.exe" in source
    assert "Get-Command pyw.exe" in source
    assert "'-3.12'" in source
    assert "Get-AuthenticodeSignature" in source
    assert "Python Software Foundation" in source
    assert "shell.Run(command, 0, True)" in source
    assert "ExecutionPolicy" not in source
    assert "pip" not in source.lower()
    assert "http" not in source.lower()


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
    assert "127.0.0.1:17709" in source
    assert "_system_executable" in source
    assert '"powershell.exe",' not in source
    assert '["tasklist.exe"' not in source
    assert "candidate generation" not in source.lower()
    assert "候选生成：关闭" in source


def test_build_portable_zip_supports_unicode_and_space_path(tmp_path: Path) -> None:
    builder = _load_path(
        "build_internal_portable", ROOT / "scripts" / "build_internal_portable.py"
    )

    output = tmp_path / "中文 目录 with spaces" / "StockWatcher-Internal-Portable.zip"
    path, digest, count = builder.build(output)
    assert path == output
    assert len(digest) == 64
    assert count == 6
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        assert len(names) == 6
        assert all(name.startswith("StockWatcher-Internal-Portable/") for name in names)
        assert names.count(
            "StockWatcher-Internal-Portable/启动 StockWatcher.vbs"
        ) == 1
        manifest = archive.read(
            "StockWatcher-Internal-Portable/MANIFEST.sha256"
        ).decode("utf-8")
        assert len(io.StringIO(manifest).readlines()) == 5
