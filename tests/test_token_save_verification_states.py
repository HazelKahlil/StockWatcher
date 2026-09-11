from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from stock_watcher.config import DataSourceSettings, HttpProfile
from stock_watcher.providers.tushare.errors import ProviderError, ProviderFailureReason
from stock_watcher.providers.tushare.rate_limit import ApplicationRequestBudget
from stock_watcher.security import (
    PRIMARY_CREDENTIAL,
    PRIMARY_LAST_GOOD_CREDENTIAL,
    PRIMARY_PENDING_CREDENTIAL,
    MemoryCredentialStore,
)
from stock_watcher.ui import data_source_status
from stock_watcher.ui.data_source_settings import DataSourceSettingsController
from stock_watcher.ui.data_source_status import (
    CREDENTIAL_INVALID,
    PENDING_VERIFICATION,
    PERMISSION_DENIED,
    VERIFIED,
    CredentialTestResult,
    LightweightCredentialTester,
)

NOW = datetime(2026, 9, 11, 14, 0)


def primary_profile() -> HttpProfile:
    return DataSourceSettings().primary_profile


def _result(
    *,
    success: bool,
    reason: str,
    verification_state: str,
    realtime_status: str = "not_checked",
) -> CredentialTestResult:
    return CredentialTestResult(
        success=success,
        tested_at=NOW,
        status_text=reason,
        permission_summary=reason,
        expires_at="未知",
        safe_reason=reason,
        realtime_status=realtime_status,
        verification_state=verification_state,
    )


def _tester_with_realtime(
    monkeypatch: pytest.MonkeyPatch,
    realtime: CredentialTestResult,
    *,
    budget: ApplicationRequestBudget | None = None,
    pro: object | None = None,
) -> LightweightCredentialTester:
    class RateLimitedPro:
        def execute(self, request: object) -> object:
            raise ProviderError(ProviderFailureReason.RATE_LIMITED, retry_after_seconds=60.0)

    monkeypatch.setattr(
        data_source_status,
        "TushareSdkProTransport",
        lambda *_args, **_kwargs: pro or RateLimitedPro(),
    )
    return LightweightCredentialTester(
        request_budget=budget,
        native_realtime_tester=SimpleNamespace(test=lambda *_args, **_kwargs: realtime),
    )


def test_pro_429_and_realtime_success_is_verified(monkeypatch: pytest.MonkeyPatch) -> None:
    tester = _tester_with_realtime(
        monkeypatch,
        _result(
            success=True,
            reason="rate_limited",
            verification_state=VERIFIED,
            realtime_status="available",
        ),
    )
    outcome = tester.test(primary_profile(), "candidate-token")
    assert outcome.verification_state == VERIFIED
    assert outcome.success
    assert "原生实时可用" in outcome.status_text


def test_pro_429_and_realtime_401_is_auth_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    tester = _tester_with_realtime(
        monkeypatch,
        _result(
            success=False,
            reason="credential_invalid",
            verification_state=CREDENTIAL_INVALID,
            realtime_status="credential_invalid",
        ),
    )
    outcome = tester.test(primary_profile(), "rejected-token")
    assert outcome.verification_state == CREDENTIAL_INVALID
    assert not outcome.success
    assert "认证失败" in outcome.status_text
    assert "未被拒绝" not in outcome.status_text


def test_pro_429_and_realtime_403_is_permission_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    tester = _tester_with_realtime(
        monkeypatch,
        _result(
            success=False,
            reason="permission_denied",
            verification_state=PERMISSION_DENIED,
            realtime_status="permission_denied",
        ),
    )
    outcome = tester.test(primary_profile(), "limited-token")
    assert outcome.verification_state == PERMISSION_DENIED
    assert not outcome.success
    assert "权限" in outcome.status_text
    assert "全部接口" not in outcome.status_text or "不代表全部接口" in outcome.permission_summary


def test_pro_429_and_realtime_timeout_is_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    tester = _tester_with_realtime(
        monkeypatch,
        _result(
            success=False,
            reason="timeout",
            verification_state=PENDING_VERIFICATION,
            realtime_status="timeout",
        ),
    )
    outcome = tester.test(primary_profile(), "pending-token")
    assert outcome.verification_state == PENDING_VERIFICATION
    assert outcome.success
    assert "尚未验证通过" in outcome.status_text


def test_existing_pro_cooldown_does_not_prove_new_token(monkeypatch: pytest.MonkeyPatch) -> None:
    budget = ApplicationRequestBudget()
    budget.pause_for(60.0, lane="pro")
    tester = _tester_with_realtime(
        monkeypatch,
        _result(
            success=False,
            reason="timeout",
            verification_state=PENDING_VERIFICATION,
            realtime_status="timeout",
        ),
        budget=budget,
    )
    outcome = tester.test(primary_profile(), "another-token")
    assert outcome.verification_state == PENDING_VERIFICATION
    assert "尚未完成基础验证" in outcome.status_text


def test_dual_channel_cooldown_does_not_call_realtime(monkeypatch: pytest.MonkeyPatch) -> None:
    budget = ApplicationRequestBudget()
    budget.pause_for(60.0, lane="pro")
    budget.pause_for(60.0, lane="realtime")
    calls: list[str] = []

    class RecordingRealtime:
        def test(self, profile: object, secret: str) -> CredentialTestResult:
            calls.append(secret)
            raise AssertionError("realtime cooldown must not start a new probe")

    tester = LightweightCredentialTester(
        request_budget=budget,
        native_realtime_tester=RecordingRealtime(),
    )
    outcome = tester.test(primary_profile(), "candidate-token")
    assert calls == []
    assert outcome.verification_state == PENDING_VERIFICATION
    assert "实时通道也在冷却" in outcome.permission_summary


def test_first_run_pending_save_writes_primary_only() -> None:
    store = MemoryCredentialStore()
    controller = DataSourceSettingsController(store=store, tester=SimpleNamespace())
    controller._stage_test_result(
        "primary",
        "first-token",
        _result(
            success=True,
            reason="rate_limited",
            verification_state=PENDING_VERIFICATION,
        ),
        primary_profile(),
        pending_epoch=controller._pending_epoch,
    )
    assert controller.commit_candidate("primary", confirmed=True)
    assert store.get(PRIMARY_CREDENTIAL) == "first-token"
    assert store.get(PRIMARY_PENDING_CREDENTIAL) is None
    assert store.get(PRIMARY_LAST_GOOD_CREDENTIAL) is None


def test_pending_replacement_keeps_previous_good_token() -> None:
    store = MemoryCredentialStore()
    store.set(PRIMARY_CREDENTIAL, "previous-good")
    store.set(PRIMARY_LAST_GOOD_CREDENTIAL, "previous-good")
    controller = DataSourceSettingsController(store=store, tester=SimpleNamespace())
    controller._stage_test_result(
        "primary",
        "unverified-new",
        _result(
            success=True,
            reason="rate_limited",
            verification_state=PENDING_VERIFICATION,
        ),
        primary_profile(),
        pending_epoch=controller._pending_epoch,
    )
    assert controller.commit_candidate("primary", confirmed=True)
    assert store.get(PRIMARY_CREDENTIAL) == "previous-good"
    assert store.get(PRIMARY_PENDING_CREDENTIAL) == "unverified-new"
    assert store.get(PRIMARY_LAST_GOOD_CREDENTIAL) == "previous-good"


def test_auth_failure_cannot_replace_previous_good() -> None:
    store = MemoryCredentialStore()
    store.set(PRIMARY_CREDENTIAL, "previous-good")
    controller = DataSourceSettingsController(store=store, tester=SimpleNamespace())
    controller._stage_test_result(
        "primary",
        "rejected-new",
        _result(
            success=False,
            reason="credential_invalid",
            verification_state=CREDENTIAL_INVALID,
        ),
        primary_profile(),
        pending_epoch=controller._pending_epoch,
    )
    assert not controller.commit_candidate("primary", confirmed=True)
    assert store.get(PRIMARY_CREDENTIAL) == "previous-good"
    assert store.get(PRIMARY_PENDING_CREDENTIAL) is None


def test_user_cancel_does_not_write_pending() -> None:
    store = MemoryCredentialStore()
    store.set(PRIMARY_CREDENTIAL, "previous-good")
    controller = DataSourceSettingsController(store=store, tester=SimpleNamespace())
    controller._stage_test_result(
        "primary",
        "unverified-new",
        _result(
            success=True,
            reason="rate_limited",
            verification_state=PENDING_VERIFICATION,
        ),
        primary_profile(),
        pending_epoch=controller._pending_epoch,
    )
    assert not controller.commit_candidate("primary", confirmed=False)
    assert store.get(PRIMARY_CREDENTIAL) == "previous-good"
    assert store.get(PRIMARY_PENDING_CREDENTIAL) is None


def test_verified_save_updates_last_good() -> None:
    store = MemoryCredentialStore()
    store.set(PRIMARY_CREDENTIAL, "previous-good")
    controller = DataSourceSettingsController(store=store, tester=SimpleNamespace())
    controller._stage_test_result(
        "primary",
        "verified-new",
        _result(success=True, reason="ok", verification_state=VERIFIED),
        primary_profile(),
        pending_epoch=controller._pending_epoch,
    )
    assert controller.commit_candidate("primary", confirmed=True)
    assert store.get(PRIMARY_CREDENTIAL) == "verified-new"
    assert store.get(PRIMARY_LAST_GOOD_CREDENTIAL) == "verified-new"


def test_timeout_discards_late_success() -> None:
    store = MemoryCredentialStore()
    store.set(PRIMARY_CREDENTIAL, "previous-good")
    controller = DataSourceSettingsController(store=store, tester=SimpleNamespace())
    epoch = controller._pending_epoch
    controller.discard_pending()
    controller._stage_test_result(
        "primary",
        "late-token",
        _result(success=True, reason="ok", verification_state=VERIFIED),
        primary_profile(),
        pending_epoch=epoch,
    )
    assert "primary" not in controller._pending
    assert not controller.commit_candidate("primary", confirmed=True)
    assert store.get(PRIMARY_CREDENTIAL) == "previous-good"


def test_restore_last_good_clears_pending_slot() -> None:
    store = MemoryCredentialStore()
    store.set(PRIMARY_CREDENTIAL, "current")
    store.set(PRIMARY_LAST_GOOD_CREDENTIAL, "previous-good")
    store.set(PRIMARY_PENDING_CREDENTIAL, "unverified-new")
    controller = DataSourceSettingsController(store=store, tester=SimpleNamespace())
    assert controller.restore_last_good_primary()
    assert store.get(PRIMARY_CREDENTIAL) == "previous-good"
    assert store.get(PRIMARY_PENDING_CREDENTIAL) is None
