"""Opaque single-device session token issuance and reconnect renewal."""

import base64
import binascii
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Final, final
from uuid import UUID

from secretary_service.enrollment import DeviceId
from secretary_service.voice.errors import VoiceAccessError
from secretary_service.voice.models import SESSION_TOKEN_TTL, SessionToken

TOKEN_NONCE_BYTES: Final = 32


@dataclass(frozen=True, slots=True)
class _TokenRecord:
    token: SessionToken
    digest: bytes


@final
class SessionTokenManager:
    """Issue opaque HMAC-authenticated tokens and retain only their digests."""

    def __init__(self, signing_key: bytes) -> None:
        """Bind tokens to a Life Engine-only signing key."""
        self._signing_key = signing_key
        self._records: dict[bytes, _TokenRecord] = {}

    def issue(self, session_id: UUID, device_id: DeviceId, now: datetime) -> SessionToken:
        """Issue one ten-minute token bound to one device and session."""
        nonce = secrets.token_bytes(TOKEN_NONCE_BYTES)
        binding = f"{session_id}\x1f{device_id}\x1f{now.isoformat()}".encode()
        signature = hmac.digest(self._signing_key, nonce + binding, "sha256")
        value = base64.urlsafe_b64encode(nonce + signature).decode()
        token = SessionToken(
            value=value,
            session_id=session_id,
            device_id=device_id,
            issued_at=now,
            expires_at=now + SESSION_TOKEN_TTL,
        )
        digest = hashlib.sha256(value.encode()).digest()
        self._records[digest] = _TokenRecord(token=token, digest=digest)
        return token

    def verify(self, value: str, now: datetime) -> SessionToken:
        """Verify token authenticity, server presence, and expiry."""
        token = self._verified_record(value).token
        if token.expires_at <= now:
            raise VoiceAccessError(reason="voice session token expired")
        return token

    def verify_for_resume(self, value: str) -> SessionToken:
        """Verify an old token without extending its original lifetime."""
        return self._verified_record(value).token

    def replace(self, value: str, now: datetime) -> SessionToken:
        """Invalidate an old token and issue a fresh token for reconnect."""
        old = self._verified_record(value)
        _ = self._records.pop(old.digest)
        return self.issue(old.token.session_id, old.token.device_id, now)

    def invalidate(self, value: str) -> None:
        """Invalidate a token after normal session finalization."""
        digest = hashlib.sha256(value.encode()).digest()
        _ = self._records.pop(digest, None)

    def _verified_record(self, value: str) -> _TokenRecord:
        digest = hashlib.sha256(value.encode()).digest()
        record = self._records.get(digest)
        if record is None:
            raise VoiceAccessError(reason="unknown voice session token")
        try:
            decoded = base64.b64decode(value, altchars=b"-_", validate=True)
        except (binascii.Error, ValueError) as error:
            raise VoiceAccessError(reason="malformed voice session token") from error
        nonce = decoded[:TOKEN_NONCE_BYTES]
        signature = decoded[TOKEN_NONCE_BYTES:]
        binding = (
            f"{record.token.session_id}\x1f{record.token.device_id}\x1f"
            f"{record.token.issued_at.isoformat()}"
        ).encode()
        expected = hmac.digest(self._signing_key, nonce + binding, "sha256")
        if len(nonce) != TOKEN_NONCE_BYTES or not hmac.compare_digest(signature, expected):
            raise VoiceAccessError(reason="invalid voice session token")
        return record
