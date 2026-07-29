from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Protocol

import keyring
from keyring.errors import PasswordDeleteError


@dataclass(frozen=True, slots=True)
class CredentialRef:
    service: str
    username: str = "api-credential"

    def __post_init__(self) -> None:
        if not self.service.startswith("StockWatcher/"):
            raise ValueError("credential service must use the StockWatcher namespace")
        if not self.username:
            raise ValueError("credential username must not be empty")


SUPER_CREDENTIAL = CredentialRef("StockWatcher/Tushare/Super")
FAST_CREDENTIAL = CredentialRef("StockWatcher/Tushare/Fast")


def credential_fingerprint(secret: str) -> str:
    if not secret:
        raise ValueError("secret must not be empty")
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:8]


class CredentialStore(Protocol):
    def get(self, reference: CredentialRef) -> str | None: ...

    def set(self, reference: CredentialRef, secret: str) -> None: ...

    def delete(self, reference: CredentialRef) -> bool: ...


@dataclass(slots=True)
class KeyringCredentialStore:
    """OS-backed credential storage. Secrets never enter app configuration or SQLite."""

    def get(self, reference: CredentialRef) -> str | None:
        return keyring.get_password(reference.service, reference.username)

    def set(self, reference: CredentialRef, secret: str) -> None:
        if not secret:
            raise ValueError("credential must not be empty")
        keyring.set_password(reference.service, reference.username, secret)

    def delete(self, reference: CredentialRef) -> bool:
        try:
            keyring.delete_password(reference.service, reference.username)
        except PasswordDeleteError:
            return False
        return True


@dataclass(slots=True)
class MemoryCredentialStore:
    """Test-only/local in-memory implementation; never persists a secret."""

    _values: dict[CredentialRef, str] = field(default_factory=dict)

    def get(self, reference: CredentialRef) -> str | None:
        return self._values.get(reference)

    def set(self, reference: CredentialRef, secret: str) -> None:
        if not secret:
            raise ValueError("credential must not be empty")
        self._values[reference] = secret

    def delete(self, reference: CredentialRef) -> bool:
        return self._values.pop(reference, None) is not None
