"""Unique Worker lease with fencing, backed by SQLite.

Only one Worker may own ``stockwatcher-worker``. The lease is acquired and
renewed inside ``BEGIN IMMEDIATE`` transactions so a Compose
``--scale worker=2`` cannot produce two active scanners.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from stock_watcher.domain import SHANGHAI
from stock_watcher.storage import SQLiteStore


class LeaseAcquireError(RuntimeError):
    """The lease is currently owned by another holder."""


class LeaseLostError(RuntimeError):
    """This holder no longer owns the lease; business work must stop."""


@dataclass(frozen=True, slots=True)
class LeaseConfig:
    lease_name: str = "stockwatcher-worker"
    heartbeat_seconds: float = 5.0
    ttl_seconds: float = 20.0


def _shanghai(value: datetime) -> datetime:
    return value.replace(tzinfo=SHANGHAI) if value.tzinfo is None else value.astimezone(SHANGHAI)


class WorkerLease:
    def __init__(
        self,
        store: SQLiteStore,
        *,
        source_commit: str,
        holder_id: str | None = None,
        config: LeaseConfig = LeaseConfig(),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.source_commit = source_commit
        self.holder_id = holder_id or uuid.uuid4().hex
        self.config = config
        self._clock = clock or (lambda: datetime.now(SHANGHAI))
        self.fencing_token: int = 0
        self.acquired_at: datetime | None = None

    @property
    def held(self) -> bool:
        return self.acquired_at is not None

    def acquire(self) -> None:
        """Acquire the lease or steal it when the previous holder expired.

        Raises LeaseAcquireError when another live holder owns it.
        """
        now = _shanghai(self._clock())
        expires_at = now + timedelta(seconds=self.config.ttl_seconds)
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT holder_id, expires_at, fencing_token "
                "FROM service_leases WHERE lease_name = ?",
                (self.config.lease_name,),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO service_leases "
                    "(lease_name, holder_id, source_commit, acquired_at, "
                    "heartbeat_at, expires_at, fencing_token) "
                    "VALUES (?, ?, ?, ?, ?, ?, 1)",
                    (
                        self.config.lease_name,
                        self.holder_id,
                        self.source_commit,
                        now.isoformat(),
                        now.isoformat(),
                        expires_at.isoformat(),
                    ),
                )
                self.fencing_token = 1
            else:
                holder_id, lease_expires_at, fencing_token = row
                if holder_id == self.holder_id:
                    self.fencing_token = int(fencing_token)
                    connection.execute(
                        "UPDATE service_leases SET heartbeat_at = ?, expires_at = ? "
                        "WHERE lease_name = ? AND holder_id = ?",
                        (
                            now.isoformat(),
                            expires_at.isoformat(),
                            self.config.lease_name,
                            self.holder_id,
                        ),
                    )
                elif (
                    (parsed_expiry := _parsed(lease_expires_at)) is not None
                    and parsed_expiry < now
                ):
                    self.fencing_token = int(fencing_token) + 1
                    connection.execute(
                        "UPDATE service_leases SET holder_id = ?, source_commit = ?, "
                        "acquired_at = ?, heartbeat_at = ?, expires_at = ?, "
                        "fencing_token = ? WHERE lease_name = ?",
                        (
                            self.holder_id,
                            self.source_commit,
                            now.isoformat(),
                            now.isoformat(),
                            expires_at.isoformat(),
                            self.fencing_token,
                            self.config.lease_name,
                        ),
                    )
                else:
                    raise LeaseAcquireError(
                        f"lease {self.config.lease_name} held by {holder_id}"
                    )
        self.acquired_at = now

    def renew(self) -> None:
        """Heartbeat and fencing check; raises LeaseLostError on mismatch."""
        if not self.held:
            raise LeaseLostError("lease was never acquired")
        now = _shanghai(self._clock())
        expires_at = now + timedelta(seconds=self.config.ttl_seconds)
        with self.store.transaction() as connection:
            cursor = connection.execute(
                "UPDATE service_leases SET heartbeat_at = ?, expires_at = ? "
                "WHERE lease_name = ? AND holder_id = ? AND fencing_token = ?",
                (
                    now.isoformat(),
                    expires_at.isoformat(),
                    self.config.lease_name,
                    self.holder_id,
                    self.fencing_token,
                ),
            )
        if cursor.rowcount != 1:
            raise LeaseLostError("lease is no longer owned by this holder")

    def release(self) -> None:
        if not self.held:
            return
        with self.store.transaction() as connection:
            connection.execute(
                "DELETE FROM service_leases "
                "WHERE lease_name = ? AND holder_id = ? AND fencing_token = ?",
                (self.config.lease_name, self.holder_id, self.fencing_token),
            )
        self.acquired_at = None

    def status(self) -> dict[str, object]:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT holder_id, source_commit, acquired_at, heartbeat_at, "
                "expires_at, fencing_token FROM service_leases WHERE lease_name = ?",
                (self.config.lease_name,),
            ).fetchone()
        if row is None:
            return {"lease_name": self.config.lease_name, "held": False}
        return {
            "lease_name": self.config.lease_name,
            "held": True,
            "holder_id": row[0],
            "source_commit": row[1],
            "acquired_at": row[2],
            "heartbeat_at": row[3],
            "expires_at": row[4],
            "fencing_token": row[5],
        }


def _parsed(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return _shanghai(parsed)
