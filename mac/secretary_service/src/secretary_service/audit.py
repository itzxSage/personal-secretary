"""Cryptographically chained, content-minimized audit records."""

import hmac
from datetime import datetime
from typing import ClassVar, final, override

from pydantic import ConfigDict

from secretary_service.models import (
    ActorId,
    CorrelationId,
    FrozenModel,
    RecordId,
    RecordKind,
    TransitionContext,
)


class AuditEntry(FrozenModel):
    """Immutable audit metadata; sensitive domain content is never embedded."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    sequence: int
    occurred_at: datetime
    action_class: str
    record_kind: RecordKind
    record_id: RecordId
    actor: ActorId
    correlation_id: CorrelationId
    source_fingerprint: str
    action_metadata: str
    previous_hash: str
    entry_hash: str


class AuditVerification(FrozenModel):
    """Successful audit verification summary."""

    entries_verified: int
    final_hash: str


class AuditMutation(FrozenModel):
    """Content-minimized inputs needed to append an audit record."""

    record_kind: RecordKind
    record_id: RecordId
    state: str
    action_class: str
    source_fingerprint: str
    context: TransitionContext


@final
class AuditTamperError(Exception):
    """Audit-chain mismatch that blocks trusted operations."""

    def __init__(self, sequence: int) -> None:
        super().__init__(sequence)
        self.sequence = sequence

    @override
    def __str__(self) -> str:
        return f"audit chain verification failed at sequence {self.sequence}"


def audit_payload(entry: AuditEntry) -> bytes:
    """Encode all chained fields in a canonical unambiguous form."""
    fields = (
        str(entry.sequence),
        entry.occurred_at.isoformat(),
        entry.action_class,
        entry.record_kind.value,
        str(entry.record_id),
        str(entry.actor),
        str(entry.correlation_id),
        entry.source_fingerprint,
        entry.action_metadata,
        entry.previous_hash,
    )
    return "\x1f".join(fields).encode()


def sign_entry(entry: AuditEntry, audit_key: bytes) -> str:
    """Sign one canonical audit entry with the audit key."""
    return hmac.digest(audit_key, audit_payload(entry), "sha256").hex()
