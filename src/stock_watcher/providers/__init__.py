from importlib import import_module
from typing import TYPE_CHECKING, Any

from .availability import (
    TUSHARE_DESCRIPTOR,
    ProviderDescriptor,
    ProviderReadiness,
    ProviderUnavailable,
    provider_descriptor,
)
from .mock import MockProvider
from .protocol import Provider
from .replay import ReplayProvider
from .synthetic import SyntheticScenarioBuilder

if TYPE_CHECKING:
    from .tdxquant import (
        TdxFailureReason,
        TdxHttpTransport,
        TdxPythonTransport,
        TdxQuantConfig,
        TdxQuantProvider,
        TdxTransportError,
    )


_TDX_EXPORTS = frozenset(
    {
        "TdxFailureReason",
        "TdxHttpTransport",
        "TdxPythonTransport",
        "TdxQuantConfig",
        "TdxQuantProvider",
        "TdxTransportError",
    }
)


def __getattr__(name: str) -> Any:
    """Load optional Windows-only TdxQuant code only when explicitly requested."""
    if name in _TDX_EXPORTS:
        return getattr(import_module(".tdxquant", __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "MockProvider",
    "Provider",
    "ProviderDescriptor",
    "ProviderReadiness",
    "ProviderUnavailable",
    "ReplayProvider",
    "SyntheticScenarioBuilder",
    "TdxQuantProvider",
    "TdxFailureReason",
    "TdxHttpTransport",
    "TdxPythonTransport",
    "TdxQuantConfig",
    "TdxTransportError",
    "TUSHARE_DESCRIPTOR",
    "provider_descriptor",
]
