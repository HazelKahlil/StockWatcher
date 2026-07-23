from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ProviderReadiness(StrEnum):
    READY = "ready"
    UNAVAILABLE = "unavailable"


class ProviderUnavailable(RuntimeError):
    """Raised when an explicitly selected provider cannot safely start."""


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    """A platform-neutral declaration of a provider's safe startup state.

    Selection code may inspect this object, but must not infer availability from
    scattered operating-system checks.  A real TdxQuant adapter may replace its
    placeholder only after the Windows M0 gate verifies SDK, authorization and
    field semantics.
    """

    name: str
    readiness: ProviderReadiness
    capabilities: frozenset[str]
    detail: str = ""

    def require_ready(self) -> None:
        if self.readiness is ProviderReadiness.UNAVAILABLE:
            raise ProviderUnavailable(self.detail)


REPLAY_DESCRIPTOR = ProviderDescriptor(
    name="replay",
    readiness=ProviderReadiness.READY,
    capabilities=frozenset({"normalized-events", "replay", "synthetic"}),
    detail="Normalized Mock/Replay data is available for local development.",
)

TDXQUANT_DESCRIPTOR = ProviderDescriptor(
    name="tdxquant",
    readiness=ProviderReadiness.UNAVAILABLE,
    capabilities=frozenset(),
    detail=(
        "TdxQuant is unavailable until the Windows M0 gate verifies the official SDK, "
        "authorization, field semantics, and recovery behavior."
    ),
)


def provider_descriptor(name: str) -> ProviderDescriptor:
    """Resolve a configured provider by declared capability, not host platform."""
    descriptors = {
        descriptor.name: descriptor for descriptor in (REPLAY_DESCRIPTOR, TDXQUANT_DESCRIPTOR)
    }
    try:
        return descriptors[name]
    except KeyError as error:
        raise ValueError(f"unknown provider: {name}") from error
