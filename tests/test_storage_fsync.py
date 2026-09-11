"""Regression coverage for durable snapshot handles without live database access."""

from __future__ import annotations

import errno
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import BinaryIO, cast

import pytest

from stock_watcher.storage import sqlite as storage_sqlite
from stock_watcher.storage.sqlite import SQLiteStore


@pytest.mark.parametrize(
    ("platform_name", "expected_mode"),
    [("nt", "r+b"), ("posix", "rb")],
)
def test_fsync_uses_non_truncating_platform_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    platform_name: str,
    expected_mode: str,
) -> None:
    target = tmp_path / "snapshot.tmp"
    payload = b"already validated snapshot bytes\x00\xff"
    target.write_bytes(payload)
    opened: list[BinaryIO] = []
    calls: list[int] = []

    class TrackedPath:
        def open(self, mode: str) -> BinaryIO:
            assert mode == expected_mode
            handle = target.open(mode)
            opened.append(handle)
            return handle

    def checked_fsync(descriptor: int) -> None:
        assert os.fstat(descriptor).st_size == len(payload)
        calls.append(descriptor)

    # Replace only this module's reference, not global os.name/pathlib behavior.
    monkeypatch.setattr(
        storage_sqlite,
        "os",
        SimpleNamespace(name=platform_name, fsync=checked_fsync),
    )
    SQLiteStore._fsync_file(cast(Path, TrackedPath()))
    assert len(calls) == 1
    assert opened[0].closed
    assert target.read_bytes() == payload


def test_fsync_native_handle_preserves_snapshot(tmp_path: Path) -> None:
    target = tmp_path / "snapshot.tmp"
    payload = b"snapshot must not be truncated"
    target.write_bytes(payload)
    SQLiteStore._fsync_file(target)
    assert target.read_bytes() == payload
    target.unlink()  # Also verifies that no Windows handle remains open.


def test_fsync_errors_are_not_swallowed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "snapshot.tmp"
    target.write_bytes(b"preserved")

    def fail_fsync(descriptor: int) -> None:
        assert os.fstat(descriptor).st_size == len(b"preserved")
        raise OSError(errno.EIO, "injected durability failure")

    monkeypatch.setattr(
        storage_sqlite,
        "os",
        SimpleNamespace(name=os.name, fsync=fail_fsync),
    )
    with pytest.raises(OSError, match="injected durability failure"):
        SQLiteStore._fsync_file(target)
    assert target.read_bytes() == b"preserved"
    target.unlink()


def test_fsync_missing_snapshot_does_not_create_it(tmp_path: Path) -> None:
    target = tmp_path / "missing.tmp"
    with pytest.raises(FileNotFoundError):
        SQLiteStore._fsync_file(target)
    assert not target.exists()


def test_guarded_transactions_remain_fenced(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "isolated.db")
    store.initialize()
    checked: list[bool] = []

    def deny(connection: sqlite3.Connection) -> None:
        checked.append(connection.in_transaction)
        raise RuntimeError("lease no longer owned")

    store.bind_write_guard(deny)
    try:
        with pytest.raises(RuntimeError, match="lease no longer owned"):
            with store.transaction() as connection:
                connection.execute("INSERT INTO notes VALUES ('forbidden', 'write')")
        assert checked == [True]
        assert store.get_note("forbidden") is None
    finally:
        store.close()
