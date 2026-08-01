from __future__ import annotations

import hashlib
import sys
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
PRIMARY_CREDENTIAL = CredentialRef("StockWatcher/Tushare/Primary")


def credential_fingerprint(secret: str) -> str:
    if not secret:
        raise ValueError("secret must not be empty")
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:8]


class CredentialStore(Protocol):
    def get(self, reference: CredentialRef) -> str | None: ...

    def set(self, reference: CredentialRef, secret: str) -> None: ...

    def delete(self, reference: CredentialRef) -> bool: ...


class CredentialStoreBackendError(RuntimeError):
    """Raised when the configured OS credential backend is not acceptable."""


@dataclass(frozen=True, slots=True)
class CredentialStoreBackendStatus:
    label: str
    backend_name: str


@dataclass(slots=True)
class KeyringCredentialStore:
    """OS-backed credential storage. Secrets never enter app configuration or SQLite.

    On macOS the generic ``keyring`` facade must resolve to the native Keychain
    backend.  Refusing an unknown fallback prevents a Token from silently
    landing in a plaintext or third-party store while the settings page claims
    it is using the system keychain.
    """

    platform: str = sys.platform

    @property
    def storage_label(self) -> str:
        return "系统钥匙串" if self.platform == "darwin" else "系统安全存储"

    def backend_status(self) -> CredentialStoreBackendStatus:
        backend = keyring.get_keyring()
        backend_name = f"{type(backend).__module__}.{type(backend).__qualname__}"
        if self.platform == "darwin" and not _is_macos_keychain_backend(backend):
            raise CredentialStoreBackendError(
                "系统钥匙串不可用；请检查 macOS Keychain 后再保存 Token。"
            )
        return CredentialStoreBackendStatus(
            label=self.storage_label,
            backend_name=backend_name,
        )

    def _verify_backend(self) -> None:
        self.backend_status()

    def get(self, reference: CredentialRef) -> str | None:
        self._verify_backend()
        return keyring.get_password(reference.service, reference.username)

    def set(self, reference: CredentialRef, secret: str) -> None:
        if not secret:
            raise ValueError("credential must not be empty")
        self._verify_backend()
        keyring.set_password(reference.service, reference.username, secret)

    def delete(self, reference: CredentialRef) -> bool:
        self._verify_backend()
        try:
            keyring.delete_password(reference.service, reference.username)
        except PasswordDeleteError:
            return False
        return True


def _is_macos_keychain_backend(backend: object) -> bool:
    """Accept the native backend, including a chainer that contains it."""
    module = type(backend).__module__
    if module.startswith("keyring.backends.macOS"):
        return True
    chained = getattr(backend, "backends", ())
    if isinstance(chained, (list, tuple)):
        return any(_is_macos_keychain_backend(item) for item in chained)
    return False


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
