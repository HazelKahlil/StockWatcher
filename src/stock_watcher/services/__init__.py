"""Headless services shared by the Web Worker and (future) Mac UI adapter.

Dependency direction: server/ui -> services -> runtime/engine/providers/storage/domain.
Nothing in this package imports stock_watcher.ui, PySide6, pyobjc, AppKit,
Foundation or the macOS keyring backend.
"""
from __future__ import annotations

from .command_service import (
    CommandService,
    CommandStatus,
    CommandType,
)
from .event_outbox import EventOutbox
from .public_state import PublicStateBuilder
from .secret_service import (
    SecretService,
    SecretServiceError,
    WrongMasterKeyError,
)
from .worker_lease import (
    LeaseAcquireError,
    LeaseConfig,
    LeaseLostError,
    WorkerLease,
)

__all__ = [
    "CommandService",
    "CommandStatus",
    "CommandType",
    "EventOutbox",
    "LeaseAcquireError",
    "LeaseConfig",
    "LeaseLostError",
    "PublicStateBuilder",
    "SecretService",
    "SecretServiceError",
    "WorkerLease",
    "WrongMasterKeyError",
]
