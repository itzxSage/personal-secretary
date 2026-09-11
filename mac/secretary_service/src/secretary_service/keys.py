"""Secret retrieval interfaces for SQLCipher, audit, backups, and connectors."""

import base64
import hashlib
import hmac
from dataclasses import dataclass
from typing import Final, Protocol, final, override

import keyring
from keyring.errors import KeyringError

KEY_BYTES: Final = 32


@final
class KeyUnavailableError(Exception):
    """Required Keychain reference was absent, locked, or malformed."""

    def __init__(self, reference: str) -> None:
        super().__init__(reference)
        self.reference = reference

    @override
    def __str__(self) -> str:
        return f"required key reference is unavailable: {self.reference}"


class KeyProvider(Protocol):
    """Narrow key retrieval boundary used by state and connectors."""

    def database_key(self) -> bytes:
        """Return the SQLCipher database key."""
        ...

    def audit_key(self) -> bytes:
        """Return the HMAC audit key."""
        ...

    def backup_wrapping_key(self) -> bytes:
        """Return the backup envelope wrapping key."""
        ...

    def connector_secret(self, reference: str) -> str:
        """Resolve a connector secret by opaque reference."""
        ...


@dataclass(frozen=True, slots=True)
class DeterministicTestKeyProvider:
    """Hermetic key provider whose output is stable for a test seed."""

    _seed: bytes

    @classmethod
    def from_seed(cls, seed: bytes) -> "DeterministicTestKeyProvider":
        """Create stable isolated keys without exposing production material."""
        return cls(_seed=hashlib.sha256(seed).digest())

    def _derive(self, purpose: bytes) -> bytes:
        return hmac.digest(self._seed, purpose, "sha256")

    def database_key(self) -> bytes:
        """Return the deterministic SQLCipher key."""
        return self._derive(b"database")

    def audit_key(self) -> bytes:
        """Return the deterministic audit key."""
        return self._derive(b"audit")

    def backup_wrapping_key(self) -> bytes:
        """Return the deterministic wrapping key."""
        return self._derive(b"backup-wrapping")

    def connector_secret(self, reference: str) -> str:
        """Reject connector lookup in the test provider."""
        raise KeyUnavailableError(reference=reference)


@dataclass(frozen=True, slots=True)
class MacOSKeychainKeyProvider:
    """Production key-reference resolver backed by the native macOS Keychain."""

    service_name: str = "com.personal-secretary.service"

    def _bytes(self, reference: str) -> bytes:
        try:
            encoded = keyring.get_password(self.service_name, reference)
        except KeyringError as error:
            raise KeyUnavailableError(reference=reference) from error
        if encoded is None:
            raise KeyUnavailableError(reference=reference)
        try:
            value = base64.b64decode(encoded, validate=True)
        except ValueError as error:
            raise KeyUnavailableError(reference=reference) from error
        if len(value) != KEY_BYTES:
            raise KeyUnavailableError(reference=reference)
        return value

    def database_key(self) -> bytes:
        """Resolve the production SQLCipher key."""
        return self._bytes("database-key")

    def audit_key(self) -> bytes:
        """Resolve the production audit key."""
        return self._bytes("audit-key")

    def backup_wrapping_key(self) -> bytes:
        """Resolve the production backup wrapping key."""
        return self._bytes("backup-wrapping-key")

    def connector_secret(self, reference: str) -> str:
        """Resolve one connector secret without exposing storage details."""
        try:
            value = keyring.get_password(self.service_name, reference)
        except KeyringError as error:
            raise KeyUnavailableError(reference=reference) from error
        if value is None:
            raise KeyUnavailableError(reference=reference)
        return value
