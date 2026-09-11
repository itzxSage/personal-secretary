"""Payload-bound worker capability leases with rotating signing keys.

A lease authorizes one worker to execute one capability for exactly one
approved proposal payload. Leases are signed by the current rotating signing
key, expire, and are consumed on first verification so replay fails closed.
"""

import hmac
from datetime import datetime, timedelta
from typing import Annotated, ClassVar, final, override
from uuid import uuid4

from pydantic import ConfigDict, StringConstraints

from secretary_service.authority import CapabilityLease, ProposalRecord, ProposalState
from secretary_service.models import (
    ActorId,
    CorrelationId,
    FrozenModel,
    NonEmpty,
    RecordId,
)
from secretary_service.storage import Clock


@final
class SigningKeyRing:
    """Rotating HMAC signing keys; only the current key verifies signatures."""

    def __init__(self, current_key: bytes) -> None:
        self._current_key = current_key
        self._generation = 0

    @property
    def generation(self) -> int:
        """Return the number of completed rotations."""
        return self._generation

    def rotate(self, new_key: bytes) -> None:
        """Rotate to a new signing key; old signatures fail closed."""
        if hmac.compare_digest(new_key, self._current_key):
            message = "new signing key must differ from the current key"
            raise ValueError(message)
        self._current_key = new_key
        self._generation += 1

    def sign(self, payload: bytes) -> str:
        """Sign one canonical payload with the current key."""
        return hmac.digest(self._current_key, payload, "sha256").hex()

    def verify(self, payload: bytes, signature: str) -> bool:
        """Verify a signature against the current key only."""
        expected = hmac.digest(self._current_key, payload, "sha256").hex()
        return hmac.compare_digest(signature, expected)


class LeaseRequest(FrozenModel):
    """Typed inputs needed to issue one capability lease."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    capability: NonEmpty
    worker_id: NonEmpty
    actor: Annotated[ActorId, StringConstraints(min_length=1)]
    correlation_id: Annotated[CorrelationId, StringConstraints(min_length=1)]
    idempotency_key: NonEmpty


class LeaseVerification(FrozenModel):
    """Successful one-shot lease verification summary."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    lease_id: RecordId
    proposal_id: RecordId
    capability: NonEmpty
    worker_id: NonEmpty
    verified_at: datetime


@final
class LeaseViolationError(Exception):
    """A lease failed signature, binding, expiry, or replay checks."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    @override
    def __str__(self) -> str:
        return self.reason


@final
class LeaseIssuer:
    """Issue and verify one-shot payload-bound capability leases."""

    def __init__(self, clock: Clock, keys: SigningKeyRing, lease_ttl: timedelta) -> None:
        self._clock = clock
        self._keys = keys
        self._lease_ttl = lease_ttl
        self._consumed: set[RecordId] = set()

    def issue(self, proposal: ProposalRecord, request: LeaseRequest) -> CapabilityLease:
        """Issue a signed lease only for an approved proposal."""
        if proposal.state != ProposalState.APPROVED:
            message = f"cannot lease proposal in state {proposal.state.value}"
            raise LeaseViolationError(message)
        now = self._clock.now()
        fact_id = RecordId(uuid4())
        expires_at = now + self._lease_ttl
        fields = (
            str(fact_id),
            str(proposal.proposal_id),
            proposal.payload_hash(),
            request.capability,
            request.worker_id,
            now.isoformat(),
            expires_at.isoformat(),
            request.idempotency_key,
            request.actor,
            request.correlation_id,
        )
        signature = self._keys.sign("\x1f".join(fields).encode())
        return CapabilityLease(
            fact_id=fact_id,
            proposal_id=proposal.proposal_id,
            payload_hash=proposal.payload_hash(),
            capability=request.capability,
            worker_id=request.worker_id,
            issued_at=now,
            expires_at=expires_at,
            idempotency_key=request.idempotency_key,
            actor=request.actor,
            correlation_id=request.correlation_id,
            signature=signature,
        )

    def verify(self, lease: CapabilityLease, proposal: ProposalRecord) -> LeaseVerification:
        """Verify signature, binding, expiry, and one-shot consumption."""
        now = self._clock.now()
        if not self._keys.verify(self._lease_payload(lease), lease.signature):
            message = "lease signature is invalid (forged or key rotated)"
            raise LeaseViolationError(message)
        if lease.proposal_id != proposal.proposal_id:
            message = "lease proposal id does not match"
            raise LeaseViolationError(message)
        if lease.payload_hash != proposal.payload_hash():
            message = "lease payload hash does not match proposal"
            raise LeaseViolationError(message)
        if lease.expires_at <= now:
            message = "lease has expired"
            raise LeaseViolationError(message)
        if lease.fact_id in self._consumed:
            message = "lease replay"
            raise LeaseViolationError(message)
        self._consumed.add(lease.fact_id)
        return LeaseVerification(
            lease_id=lease.fact_id,
            proposal_id=lease.proposal_id,
            capability=lease.capability,
            worker_id=lease.worker_id,
            verified_at=now,
        )

    def _lease_payload(self, lease: CapabilityLease) -> bytes:
        fields = (
            str(lease.fact_id),
            str(lease.proposal_id),
            lease.payload_hash,
            lease.capability,
            lease.worker_id,
            lease.issued_at.isoformat(),
            lease.expires_at.isoformat(),
            lease.idempotency_key,
            lease.actor,
            lease.correlation_id,
        )
        return "\x1f".join(fields).encode()
