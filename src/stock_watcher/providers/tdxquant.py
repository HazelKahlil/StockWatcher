from __future__ import annotations

from collections.abc import Iterator

from stock_watcher.domain import MarketEvent

from .availability import TDXQUANT_DESCRIPTOR, ProviderDescriptor


class TdxQuantProvider:
    """Intentional Windows M0 placeholder; it never imports or calls a vendor SDK."""

    name = "tdxquant"
    version = "unverified"
    descriptor: ProviderDescriptor = TDXQUANT_DESCRIPTOR

    def events(self) -> Iterator[MarketEvent]:
        self.descriptor.require_ready()
        yield from ()
