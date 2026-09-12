"""Application encryption for cloud records, independently of database encryption."""

import base64
import json
import os
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretBytes

KEY_BYTES: Final = 32


@dataclass(frozen=True, slots=True)
class CloudStateKeys:
    """Unwrapped data and audit keys supplied by the trusted key provider.

    These are not derived from a tenant identifier. Production acquisition and
    rotation require KMS integration; synthetic tests inject independent keys.
    """

    data: SecretBytes
    audit: SecretBytes

    def __post_init__(self) -> None:
        """Reject short or shared-purpose keys before opening state."""
        if (
            len(self.data.get_secret_value()) != KEY_BYTES
            or len(self.audit.get_secret_value()) != KEY_BYTES
        ):
            message = "cloud state requires two 256-bit keys"
            raise ValueError(message)
        if self.data == self.audit:
            message = "data and audit keys must be independent"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class CloudCipher:
    """Bind authenticated ciphertext to its cell, record family, ID and version."""

    tenant_id: UUID
    keys: CloudStateKeys

    def _aad(self, identity: tuple[str, ...]) -> bytes:
        return json.dumps(["lifeos.cloud.v1", str(self.tenant_id), *identity]).encode()

    def seal(self, plaintext: str, identity: tuple[str, ...]) -> str:
        """Encrypt using a fresh nonce; callers persist the complete envelope."""
        nonce = os.urandom(12)
        encrypted = AESGCM(self.keys.data.get_secret_value()).encrypt(
            nonce, plaintext.encode(), self._aad(identity)
        )
        return base64.b64encode(nonce + encrypted).decode("ascii")

    def open(self, envelope: str, identity: tuple[str, ...]) -> str:
        """Authenticate location before releasing plaintext; corruption raises."""
        encoded = base64.b64decode(envelope, validate=True)
        return (
            AESGCM(self.keys.data.get_secret_value())
            .decrypt(encoded[:12], encoded[12:], self._aad(identity))
            .decode()
        )
