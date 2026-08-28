from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, cast

import pytest

import stock_watcher.security.credential_store as credential_module
import stock_watcher.ui.app as app_module
from stock_watcher.config import NativeRealtimeProfile
from stock_watcher.providers.tushare.errors import ProviderError, ProviderFailureReason
from stock_watcher.providers.tushare.native_realtime_transport import NativeRealtimeTransport
from stock_watcher.providers.tushare.transport_protocol import TransportRequest
from stock_watcher.security import (
    PRIMARY_CREDENTIAL,
    CredentialStoreBackendError,
    KeyringCredentialStore,
    MemoryCredentialStore,
)
from stock_watcher.ui.tushare_v1_session import TushareV1Session


def test_windows_single_instance_name_is_stable_across_pids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from stock_watcher.ui.single_instance import _default_instance_name

    monkeypatch.setattr(os, "getpid", lambda: 111)
    first = _default_instance_name()
    monkeypatch.setattr(os, "getpid", lambda: 222)
    second = _default_instance_name()
    assert first == second
    assert first.startswith("stockwatcher-")


def test_windows_app_enables_single_instance_and_app_mutex() -> None:
    source = Path(app_module.__file__).read_text(encoding="utf-8")
    assert 'sys.platform in {"darwin", "win32"}' in source
    assert "acquire_app_mutex()" in source
    assert "raise_existing_window()" in source
    assert "os._exit(exit_code)" in source
    runtime = Path("src/stock_watcher/ui/windows_runtime.py").read_text(encoding="utf-8")
    assert "instance.lock" in runtime
    assert "EnumWindows" in runtime
    installer = Path("packaging/windows/StockWatcher.iss").read_text(encoding="utf-8")
    assert "AppMutex=StockWatcher.AppMutex" in installer
    assert "PrepareToInstall" in installer
    assert "taskkill.exe" in installer


def test_windows_font_preferences_do_not_force_macos_families() -> None:
    assert app_module.application_font_candidates("win32") == (
        "Microsoft YaHei UI",
        "Microsoft YaHei",
        "Segoe UI",
    )
    assert "font-family: -apple-system" not in app_module.STYLE_SHEET


def test_windows_keyring_requires_credential_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keyring_module = cast(Any, getattr(credential_module, "keyring"))
    native = type("WinVaultKeyring", (), {"__module__": "keyring.backends.Windows"})()
    monkeypatch.setattr(keyring_module, "get_keyring", lambda: native)
    store = KeyringCredentialStore(platform="win32")
    assert store.storage_label == "Windows 凭据管理器"
    assert store.backend_status().label == "Windows 凭据管理器"

    fallback = type("PlaintextBackend", (), {"__module__": "keyrings.alt.file"})()
    monkeypatch.setattr(keyring_module, "get_keyring", lambda: fallback)
    with pytest.raises(CredentialStoreBackendError, match="Windows 凭据管理器不可用"):
        store.backend_status()


def test_native_realtime_sdk_call_has_a_hard_timeout() -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingClient:
        version = "test"

        def configure(self, _token: str, _verify_url: str) -> None:
            return

        def fetch(
            self,
            _codes: tuple[str, ...],
            *,
            source: str,
        ) -> list[dict[str, object]]:
            assert source == "sina"
            entered.set()
            release.wait(2.0)
            return []

    profile = NativeRealtimeProfile().model_copy(
        update={"request_timeout_seconds": 0.03}
    )
    transport = NativeRealtimeTransport(
        profile,
        lambda: "test-only-token",
        client=BlockingClient(),
    )
    request = TransportRequest(
        endpoint="tushare.realtime_quote:sina",
        api_name="realtime_quote",
        params={"ts_code": "000001.SZ"},
        realtime=True,
    )
    started = time.monotonic()
    try:
        with pytest.raises(ProviderError) as caught:
            transport.execute(request)
        assert caught.value.reason is ProviderFailureReason.TIMEOUT
        assert entered.wait(0.2)
        assert time.monotonic() - started < 0.5
    finally:
        release.set()


@pytest.mark.parametrize("scenario", ("layout", "close", "popup", "settings", "history"))
def test_windows_qt_stability_probe_isolated(
    scenario: str,
    tmp_path: Path,
) -> None:
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    probe = Path(__file__).with_name("windows_ui_probe.py")
    completed = subprocess.run(
        [sys.executable, str(probe), scenario, str(tmp_path / scenario)],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, (
        f"scenario={scenario}\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )


def test_session_shutdown_request_cancels_scan_and_is_idempotent(tmp_path: Path) -> None:
    credentials = MemoryCredentialStore()
    credentials.set(PRIMARY_CREDENTIAL, "test-only-token")
    session = TushareV1Session(
        tmp_path / "session.sqlite3",
        credential_store=credentials,
    )

    class Runtime:
        cancellations = 0

        def request_scan_cancellation(self) -> None:
            self.cancellations += 1

    runtime = Runtime()
    session._runtime = cast(Any, runtime)
    session.request_shutdown()
    session.request_shutdown()
    assert session.shutdown_requested
    assert runtime.cancellations == 1
    session.shutdown()
    session.shutdown()
