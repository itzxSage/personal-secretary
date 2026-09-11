"""Signed, short-lived, request-scoped worker credential handles."""

import hmac
from datetime import timedelta
from typing import final, override

from secretary_service.commander import CredentialGrant, WorkerRequest
from secretary_service.storage import Clock


@final
class CredentialGateError(Exception):
    """A worker credential failed scope, signature, expiry, or replay checks."""

    def __init__(self, reason: str) -> None:
        """Retain the machine-readable denial reason."""
        super().__init__(reason)
        self.reason = reason

    @override
    def __str__(self) -> str:
        return self.reason


@final
class ScopedCredentialIssuer:
    """Issue and consume opaque credential handles bound to one request."""

    def __init__(self, clock: Clock, signing_key: bytes, ttl: timedelta) -> None:
        """Configure a signing key and deterministic validity window."""
        self._clock = clock
        self._signing_key = signing_key
        self._ttl = ttl
        self._consumed: set[str] = set()

    def issue(self, request: WorkerRequest) -> tuple[CredentialGrant, ...]:
        """Issue one signed opaque handle for each requested reference."""
        now = self._clock.now()
        expires_at = now + self._ttl
        return tuple(
            CredentialGrant(
                reference=reference,
                handle=self._sign(request, reference, now.isoformat(), expires_at.isoformat()),
                issued_at=now,
                expires_at=expires_at,
                request_id=request.request_id,
                capability=request.capability,
            )
            for reference in request.credential_references
        )

    def consume(
        self,
        request: WorkerRequest,
        grants: tuple[CredentialGrant, ...],
        maximum_ttl: timedelta,
    ) -> None:
        """Verify the exact grant set and consume all handles atomically."""
        references = tuple(grant.reference for grant in grants)
        if references != request.credential_references:
            reason = "credential_scope_mismatch"
            raise CredentialGateError(reason)
        now = self._clock.now()
        for grant in grants:
            if grant.request_id != request.request_id or grant.capability is not request.capability:
                reason = "credential_scope_mismatch"
                raise CredentialGateError(reason)
            if grant.issued_at is None or grant.expires_at - grant.issued_at > maximum_ttl:
                reason = "credential_ttl_exceeded"
                raise CredentialGateError(reason)
            if grant.expires_at <= now:
                reason = "credential_expired"
                raise CredentialGateError(reason)
            expected = self._sign(
                request,
                grant.reference,
                grant.issued_at.isoformat(),
                grant.expires_at.isoformat(),
            )
            if not hmac.compare_digest(expected, grant.handle):
                reason = "credential_forged"
                raise CredentialGateError(reason)
            if grant.handle in self._consumed:
                reason = "credential_replay"
                raise CredentialGateError(reason)
        self._consumed.update(grant.handle for grant in grants)

    def _sign(
        self,
        request: WorkerRequest,
        reference: str,
        issued_at: str,
        expires_at: str,
    ) -> str:
        fields = (
            str(request.request_id),
            request.worker_id,
            request.capability.value,
            reference,
            issued_at,
            expires_at,
        )
        return hmac.digest(self._signing_key, "\x1f".join(fields).encode(), "sha256").hex()
