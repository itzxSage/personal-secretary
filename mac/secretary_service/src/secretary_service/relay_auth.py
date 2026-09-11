"""Domain-separated request signatures bound to the actual mTLS peer certificate."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from secretary_service.enrollment import (
    DeviceId,
    DeviceNotFoundError,
    ForgedDeviceError,
    RevokedDeviceError,
)

if TYPE_CHECKING:
    from uuid import UUID

    from secretary_service.authority_consumption import ConsumptionStore
    from secretary_service.enrollment import DeviceRegistry
    from secretary_service.storage import Clock

MAX_REQUEST_AGE = 60
FUTURE_CLOCK_TOLERANCE = 5


class RequestAuthenticationError(Exception):
    """Authentication failed without exposing enrollment or signature details."""


@dataclass(frozen=True)
class RequestProof:
    """Canonical request identifiers and the detached Ed25519 signature."""

    device_id: UUID
    request_id: UUID
    issued_at: int
    signature: str

    def signing_bytes(self, method: str, target: str, body: bytes) -> bytes:
        """Match the Swift client exactly, including path/query and raw body digest."""
        fields = (
            "lifeos.request.v1",
            method,
            target,
            str(self.device_id),
            str(self.request_id),
            str(self.issued_at),
            hashlib.sha256(body).hexdigest(),
        )
        if any("\n" in field or "\r" in field for field in fields):
            raise RequestAuthenticationError
        return "\n".join(fields).encode("ascii")


@dataclass(frozen=True)
class RequestAuthenticator:
    """Require current enrollment, TLS binding, freshness, signature, and durable nonce."""

    devices: DeviceRegistry
    consumption: ConsumptionStore
    clock: Clock

    def verify(
        self,
        proof: RequestProof,
        method: str,
        target: str,
        body: bytes,
        peer_fingerprint: str,
    ) -> UUID:
        """Reject stale, altered, revoked, mismatched-certificate, or replayed requests."""
        age = self.clock.now().timestamp() - proof.issued_at
        if not -FUTURE_CLOCK_TOLERANCE <= age <= MAX_REQUEST_AGE:
            raise RequestAuthenticationError
        try:
            device = self.devices.verify_mtls_identity(
                DeviceId(str(proof.device_id)), peer_fingerprint
            )
            if device.approval_public_key is None:
                raise RequestAuthenticationError
            key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(device.approval_public_key))
            key.verify(bytes.fromhex(proof.signature), proof.signing_bytes(method, target, body))
        except (
            ValueError,
            UnicodeError,
            InvalidSignature,
            DeviceNotFoundError,
            ForgedDeviceError,
            RevokedDeviceError,
        ) as error:
            raise RequestAuthenticationError from error
        if not self.consumption.consume((("relay.request", str(proof.request_id)),)):
            raise RequestAuthenticationError
        return proof.device_id
