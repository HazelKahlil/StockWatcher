"""Encrypted Tushare token handling.

The master key never enters Git, chat, configuration, logs, the database or
the delivery package: it is read from a file mounted as a Docker secret on the
VPS (``/run/secrets/stockwatcher_master_key``). Tokens are AES-256-GCM
encrypted with AAD binding secret name + key version + environment so a
ciphertext cannot be replayed against another slot.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from stock_watcher.domain import SHANGHAI
from stock_watcher.storage import SQLiteStore

TUSHARE_PRIMARY_SECRET = "tushare.primary"
SECRET_REQUEST_TTL_SECONDS = 600.0
SECRET_REQUEST_RETENTION_DAYS = 7
MASTER_KEY_BYTES = 32


class SecretServiceError(RuntimeError):
    pass


class WrongMasterKeyError(SecretServiceError):
    pass


def _shanghai(value: datetime) -> datetime:
    return value.replace(tzinfo=SHANGHAI) if value.tzinfo is None else value.astimezone(SHANGHAI)


def fingerprint(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:8]


def load_master_key(path: Path) -> bytes:
    """Read and validate the 32-byte base64url Docker secret file."""
    try:
        raw = path.read_bytes().strip()
    except OSError as error:
        raise SecretServiceError(f"master key file unreadable: {error}") from error
    if len(raw) < 24:
        raise SecretServiceError("master key file is too short")
    try:
        key = base64.urlsafe_b64decode(raw + b"=" * (-len(raw) % 4))
    except ValueError as error:
        raise SecretServiceError("master key file is not valid base64url") from error
    if len(key) != MASTER_KEY_BYTES:
        raise SecretServiceError(
            f"master key must decode to {MASTER_KEY_BYTES} bytes"
        )
    return key


class SecretService:
    def __init__(
        self,
        store: SQLiteStore,
        *,
        master_key: bytes,
        environment: str = "production",
        key_version: int = 1,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if len(master_key) != MASTER_KEY_BYTES:
            raise SecretServiceError(
                f"master key must be {MASTER_KEY_BYTES} bytes"
            )
        if key_version < 1:
            raise ValueError("key_version must be positive")
        self.store = store
        self._key = master_key
        self._environment = environment
        self.key_version = key_version
        self._clock = clock or (lambda: datetime.now(SHANGHAI))

    # -- cryptography ----------------------------------------------------

    def _aad(self, secret_name: str, purpose: str) -> bytes:
        return (
            f"stockwatcher-secret:{secret_name}:{purpose}:"
            f"{self.key_version}:{self._environment}"
        ).encode()

    def _encrypt(self, plaintext: str, secret_name: str, purpose: str) -> tuple[str, str]:
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._key).encrypt(
            self._aad(secret_name, purpose),
            plaintext.encode("utf-8"),
            nonce,
        )
        return (
            base64.b64encode(ciphertext).decode("ascii"),
            base64.b64encode(nonce).decode("ascii"),
        )

    def _decrypt(
        self,
        ciphertext_b64: str,
        nonce_b64: str,
        secret_name: str,
        purpose: str,
    ) -> str:
        try:
            plaintext = AESGCM(self._key).decrypt(
                self._aad(secret_name, purpose),
                base64.b64decode(ciphertext_b64),
                base64.b64decode(nonce_b64),
            )
        except (InvalidTag, ValueError) as error:
            raise WrongMasterKeyError(
                "decryption failed (wrong master key or tampered ciphertext)"
            ) from error
        return plaintext.decode("utf-8")

    # -- short-lived requests --------------------------------------------

    def create_request(
        self,
        *,
        candidate_token: str,
        purpose: str,
        requested_by: int,
    ) -> dict[str, Any]:
        """Encrypt a candidate token and store a short-lived secret request.

        The plaintext token is immediately encrypted and never persisted;
        only the request id and fingerprint leave this method.
        """
        if purpose not in {"token_test", "token_update"}:
            raise ValueError("purpose must be token_test or token_update")
        if not candidate_token:
            raise ValueError("candidate token must not be empty")
        request_id = uuid.uuid4().hex
        ciphertext_b64, nonce_b64 = self._encrypt(
            candidate_token,
            TUSHARE_PRIMARY_SECRET,
            purpose,
        )
        now = _shanghai(self._clock())
        expires_at = now + timedelta(seconds=SECRET_REQUEST_TTL_SECONDS)
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT INTO secret_requests "
                "(request_id, purpose, ciphertext_b64, nonce_b64, key_version, "
                "fingerprint, requested_by, created_at, expires_at, consumed_at, "
                "status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'pending')",
                (
                    request_id,
                    purpose,
                    ciphertext_b64,
                    nonce_b64,
                    self.key_version,
                    fingerprint(candidate_token),
                    requested_by,
                    now.isoformat(),
                    expires_at.isoformat(),
                ),
            )
        return {
            "request_id": request_id,
            "fingerprint": fingerprint(candidate_token),
            "expires_at": expires_at.isoformat(),
        }

    def consume_request(self, request_id: str) -> tuple[str, str]:
        """Worker-side: decrypt a pending request and mark it consumed.

        Returns ``(plaintext, purpose)``. The ciphertext is wiped immediately
        after consumption.
        """
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT purpose, ciphertext_b64, nonce_b64, key_version, status, "
                "expires_at FROM secret_requests WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if row is None:
                raise SecretServiceError("secret request not found")
            purpose, ciphertext_b64, nonce_b64, key_version, status, expires_at = row
            now = _shanghai(self._clock())
            if status != "pending":
                raise SecretServiceError(f"secret request status is {status}")
            if _shanghai(datetime.fromisoformat(expires_at)) < now:
                connection.execute(
                    "UPDATE secret_requests SET status = 'expired' "
                    "WHERE request_id = ?",
                    (request_id,),
                )
                raise SecretServiceError("secret request expired")
            try:
                plaintext = self._decrypt(
                    ciphertext_b64,
                    nonce_b64,
                    TUSHARE_PRIMARY_SECRET,
                    purpose,
                )
            except WrongMasterKeyError:
                connection.execute(
                    "UPDATE secret_requests SET status = 'failed' "
                    "WHERE request_id = ?",
                    (request_id,),
                )
                raise
            connection.execute(
                "UPDATE secret_requests SET status = 'consumed', consumed_at = ?, "
                "ciphertext_b64 = '', nonce_b64 = '' WHERE request_id = ?",
                (now.isoformat(), request_id),
            )
        return plaintext, purpose

    def fail_request(self, request_id: str) -> None:
        with self.store.transaction() as connection:
            connection.execute(
                "UPDATE secret_requests SET status = 'failed', "
                "ciphertext_b64 = '', nonce_b64 = '' WHERE request_id = ?",
                (request_id,),
            )

    # -- active/previous slots ------------------------------------------

    def store_active(
        self,
        *,
        token: str,
        updated_by: int | None = None,
        capability: dict[str, Any] | None = None,
    ) -> None:
        """Move current active to previous and store the new active token."""
        ciphertext_b64, nonce_b64 = self._encrypt(
            token,
            TUSHARE_PRIMARY_SECRET,
            "active",
        )
        now = _shanghai(self._clock())
        with self.store.transaction() as connection:
            connection.execute(
                "DELETE FROM encrypted_secrets "
                "WHERE secret_name = ? AND slot = 'previous'",
                (TUSHARE_PRIMARY_SECRET,),
            )
            connection.execute(
                "UPDATE encrypted_secrets SET slot = 'previous', status = 'previous' "
                "WHERE secret_name = ? AND slot = 'active'",
                (TUSHARE_PRIMARY_SECRET,),
            )
            connection.execute(
                "INSERT INTO encrypted_secrets "
                "(secret_name, slot, ciphertext_b64, nonce_b64, key_version, "
                "fingerprint, status, updated_by, updated_at, last_tested_at, "
                "capability_json) VALUES (?, 'active', ?, ?, ?, ?, 'active', ?, ?, ?, ?) "
                "ON CONFLICT(secret_name, slot) DO UPDATE SET "
                "ciphertext_b64 = excluded.ciphertext_b64, "
                "nonce_b64 = excluded.nonce_b64, "
                "key_version = excluded.key_version, "
                "fingerprint = excluded.fingerprint, "
                "status = 'active', "
                "updated_by = excluded.updated_by, "
                "updated_at = excluded.updated_at, "
                "last_tested_at = excluded.last_tested_at, "
                "capability_json = excluded.capability_json",
                (
                    TUSHARE_PRIMARY_SECRET,
                    ciphertext_b64,
                    nonce_b64,
                    self.key_version,
                    fingerprint(token),
                    updated_by,
                    now.isoformat(),
                    now.isoformat(),
                    json_dumps(capability),
                ),
            )

    def active_token(self) -> str | None:
        return self._slot_token("active")

    def previous_token(self) -> str | None:
        return self._slot_token("previous")

    def _slot_token(self, slot: str) -> str | None:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT ciphertext_b64, nonce_b64 FROM encrypted_secrets "
                "WHERE secret_name = ? AND slot = ?",
                (TUSHARE_PRIMARY_SECRET, slot),
            ).fetchone()
        if row is None:
            return None
        try:
            return self._decrypt(
                str(row[0]),
                str(row[1]),
                TUSHARE_PRIMARY_SECRET,
                "active",
            )
        except WrongMasterKeyError:
            return None

    def active_fingerprint(self) -> str | None:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT fingerprint, capability_json, updated_at FROM encrypted_secrets "
                "WHERE secret_name = ? AND slot = 'active'",
                (TUSHARE_PRIMARY_SECRET,),
            ).fetchone()
        if row is None:
            return None
        return str(row[0])

    def active_metadata(self) -> dict[str, Any] | None:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT fingerprint, key_version, status, updated_at, last_tested_at, "
                "capability_json FROM encrypted_secrets "
                "WHERE secret_name = ? AND slot = 'active'",
                (TUSHARE_PRIMARY_SECRET,),
            ).fetchone()
        if row is None:
            return None
        return {
            "fingerprint": row[0],
            "key_version": row[1],
            "status": row[2],
            "updated_at": row[3],
            "last_tested_at": row[4],
            "capability": json_loads(str(row[5])),
        }

    # -- maintenance -----------------------------------------------------

    def expire_requests(self, *, now: datetime | None = None) -> int:
        current = _shanghai(now or self._clock())
        with self.store.transaction() as connection:
            cursor = connection.execute(
                "UPDATE secret_requests SET status = 'expired', "
                "ciphertext_b64 = '', nonce_b64 = '' "
                "WHERE status = 'pending' AND expires_at <= ?",
                (current.isoformat(),),
            )
            return max(cursor.rowcount, 0)

    def prune_requests(self, *, now: datetime | None = None) -> int:
        current = _shanghai(now or self._clock())
        cutoff = (current - timedelta(days=SECRET_REQUEST_RETENTION_DAYS)).isoformat()
        with self.store.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM secret_requests WHERE created_at < ?",
                (cutoff,),
            )
            return max(cursor.rowcount, 0)


def json_dumps(value: dict[str, Any] | None) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def json_loads(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
