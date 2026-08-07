from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from stock_watcher.config import DataSourceConfigRepository
from stock_watcher.security import (
    PRIMARY_CREDENTIAL,
    SUPER_CREDENTIAL,
    KeyringCredentialStore,
    MemoryCredentialStore,
    credential_fingerprint,
)
from stock_watcher.ui.data_source_settings import DataSourceSettingsController
from stock_watcher.ui.data_source_status import CredentialTestResult


class RecordingTester:
    def __init__(self, success: bool) -> None:
        self.success = success
        self.seen_secret: str | None = None

    def test(self, profile: object, secret: str) -> CredentialTestResult:
        from datetime import datetime

        self.seen_secret = secret
        return CredentialTestResult(
            success=self.success,
            tested_at=datetime.now().astimezone(),
            status_text="通过" if self.success else "凭据无效",
            permission_summary="安全摘要",
            expires_at="未知",
            safe_reason=None if self.success else "credential_invalid",
        )


def test_fingerprint_is_short_and_does_not_reveal_secret() -> None:
    secret = "not-a-production-key"
    fingerprint = credential_fingerprint(secret)
    assert len(fingerprint) == 8
    assert secret not in fingerprint


def test_keyring_cache_reads_without_entering_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    store = KeyringCredentialStore(platform="darwin")
    secret = "cache-only-secret"
    store._cache(PRIMARY_CREDENTIAL, secret)

    import stock_watcher.security.credential_store as credential_module

    keyring_module = cast(Any, getattr(credential_module, "keyring"))
    monkeypatch.setattr(
        keyring_module,
        "get_password",
        lambda *_args: (_ for _ in ()).throw(AssertionError("backend read")),
    )

    assert store.get(PRIMARY_CREDENTIAL) == secret
    assert store.get_cached(PRIMARY_CREDENTIAL) == (True, secret)


def test_keyring_cache_updates_on_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    store = KeyringCredentialStore(platform="darwin")
    native_backend = type("Keyring", (), {"__module__": "keyring.backends.macOS"})()
    import stock_watcher.security.credential_store as credential_module

    keyring_module = cast(Any, getattr(credential_module, "keyring"))
    monkeypatch.setattr(keyring_module, "get_keyring", lambda: native_backend)
    monkeypatch.setattr(keyring_module, "delete_password", lambda *_args: None)
    store._cache(PRIMARY_CREDENTIAL, "cache-only-secret")

    assert store.delete(PRIMARY_CREDENTIAL)
    assert store.get_cached(PRIMARY_CREDENTIAL) == (True, None)


def test_failed_candidate_test_preserves_existing_credential() -> None:
    store = MemoryCredentialStore()
    store.set(SUPER_CREDENTIAL, "old-secret")
    tester = RecordingTester(False)
    controller = DataSourceSettingsController(store=store, tester=tester)
    result = controller.test_candidate(
        "super",
        "new-secret",
        base_url="https://ai-tool.indevs.in",
        use_system_proxy=False,
    )
    assert not result.success
    assert not controller.commit_candidate("super", confirmed=True)
    assert store.get(SUPER_CREDENTIAL) == "old-secret"


def test_successful_candidate_requires_confirmation_before_atomic_replace() -> None:
    store = MemoryCredentialStore()
    store.set(SUPER_CREDENTIAL, "old-secret")
    tester = RecordingTester(True)
    switched: list[str] = []
    controller = DataSourceSettingsController(
        store=store,
        tester=tester,
        on_provider_changed=lambda mode: switched.append(mode.value),
    )
    controller.test_candidate(
        "super",
        "new-secret",
        base_url="https://ai-tool.indevs.in",
        use_system_proxy=False,
    )
    assert not controller.commit_candidate("super", confirmed=False)
    assert store.get(SUPER_CREDENTIAL) == "old-secret"
    assert controller.commit_candidate("super", confirmed=True)
    assert store.get(SUPER_CREDENTIAL) == "new-secret"
    assert switched == ["super"]


def test_pending_secret_is_cleared_when_dialog_is_abandoned() -> None:
    store = MemoryCredentialStore()
    tester = RecordingTester(True)
    controller = DataSourceSettingsController(store=store, tester=tester)
    controller.test_candidate(
        "super",
        "temporary-secret",
        base_url="https://ai-tool.indevs.in",
        use_system_proxy=False,
    )
    controller.discard_pending()
    assert not controller.commit_candidate("super", confirmed=True)
    assert store.get(SUPER_CREDENTIAL) is None


def test_secret_is_not_written_to_logs(caplog: object) -> None:
    store = MemoryCredentialStore()
    controller = DataSourceSettingsController(store=store, tester=RecordingTester(False))
    secret = "unique-never-log-secret"
    controller.test_candidate(
        "super",
        secret,
        base_url="https://ai-tool.indevs.in",
        use_system_proxy=False,
    )
    assert secret not in str(caplog)


def test_successful_replace_persists_only_non_secret_profile(tmp_path: Path) -> None:
    repository = DataSourceConfigRepository(tmp_path / "data-sources.yaml")
    store = MemoryCredentialStore()
    controller = DataSourceSettingsController(
        store=store,
        tester=RecordingTester(True),
        repository=repository,
    )
    secret = "never-write-this-secret"
    controller.test_candidate(
        "super",
        secret,
        base_url="https://example.invalid",
        use_system_proxy=True,
    )
    assert controller.commit_candidate("super", confirmed=True)
    rendered = repository.path.read_text(encoding="utf-8")
    assert secret not in rendered
    assert "https://example.invalid" in rendered
    assert "use_system_proxy: true" in rendered
