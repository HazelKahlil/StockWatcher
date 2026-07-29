from __future__ import annotations

import importlib

import pytest

from stock_watcher.providers import (
    ProviderReadiness,
    ProviderUnavailable,
    TdxQuantProvider,
    provider_descriptor,
)


@pytest.mark.parametrize(
    "module",
    (
        "stock_watcher.domain",
        "stock_watcher.engine",
        "stock_watcher.storage",
        "stock_watcher.providers.tushare.provider",
        "stock_watcher.providers.tushare.response_parser",
        "stock_watcher.providers.tushare.capability_router",
        "stock_watcher.ui.presenter",
    ),
)
def test_shared_layers_import_without_windows_or_tdxquant(module: str) -> None:
    """Core behavior remains usable without a Windows-only provider installation."""
    assert importlib.import_module(module)


def test_provider_selection_uses_declared_readiness_not_host_platform() -> None:
    replay = provider_descriptor("replay")
    assert replay.readiness is ProviderReadiness.READY
    assert "normalized-events" in replay.capabilities

    tdxquant = provider_descriptor("tdxquant")
    assert tdxquant.readiness is ProviderReadiness.PREFLIGHT_REQUIRED
    assert "official-loopback-http" in tdxquant.capabilities
    with pytest.raises(ProviderUnavailable, match="preflight"):
        tuple(TdxQuantProvider().events())

    tushare = provider_descriptor("tushare")
    assert tushare.readiness is ProviderReadiness.PREFLIGHT_REQUIRED
    assert "cross-platform-https" in tushare.capabilities
    with pytest.raises(ProviderUnavailable, match="data-gate M0"):
        tushare.require_ready()


def test_unknown_provider_is_rejected_explicitly() -> None:
    with pytest.raises(ValueError, match="unknown provider"):
        provider_descriptor("unapproved-provider")
