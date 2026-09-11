"""SQLCipher persistence for governed memory records."""

import hmac
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import datetime
from typing import final

from sqlcipher3 import dbapi2 as sqlcipher

from secretary_service.audit import AuditMutation
from secretary_service.keys import KeyProvider
from secretary_service.ledger import AuditLedger
from secretary_service.memory import (
    Clock,
    MemoryCorrection,
    MemoryDeletion,
    MemoryNotFoundError,
    MemoryRecord,
    MemoryRemovalReason,
    MemoryTombstone,
    RetrievalScope,
    StaleMemoryRevisionError,
)
from secretary_service.models import RecordId, RecordKind, TransitionContext


@final
class MemoryAuditor:
    """Create content-minimized memory audit entries."""

    def __init__(self, keys: KeyProvider, ledger: AuditLedger) -> None:
        """Bind audit keys and the chained ledger."""
        self._keys = keys
        self._ledger = ledger

    def fingerprint(self, record: MemoryRecord) -> str:
        """Return the keyed fingerprint allowed to survive content erasure."""
        return hmac.digest(
            self._keys.audit_key(), record.model_dump_json().encode(), "sha256"
        ).hex()

    def verify(self) -> None:
        """Block memory mutation when the audit chain is untrusted."""
        _ = self._ledger.verify()

    def append(
        self,
        record: MemoryRecord,
        action: str,
        fingerprint: str,
        context: TransitionContext,
    ) -> None:
        """Append an audit entry without embedding memory content."""
        self._ledger.append(
            AuditMutation(
                record_kind=RecordKind.NORMALIZED_FACT,
                record_id=record.memory_id,
                state=action,
                action_class=f"memory.{action}",
                source_fingerprint=fingerprint,
                context=context,
            )
        )


@final
class MemoryLifecycle:
    """Clock and narrowly scoped physical-deletion authorization."""

    def __init__(
        self,
        clock: Clock,
        authorize_delete: Callable[[], None],
        revoke_delete: Callable[[], None],
    ) -> None:
        """Bind retention time and the store deletion gate."""
        self._clock = clock
        self._authorize_delete = authorize_delete
        self._revoke_delete = revoke_delete

    def now(self) -> datetime:
        """Return the current retention time."""
        return self._clock.now()

    @contextmanager
    def deletion(self) -> Generator[None]:
        """Authorize physical deletion only for one bounded operation."""
        self._authorize_delete()
        try:
            yield
        finally:
            self._revoke_delete()


@final
class MemoryRepository:
    """Persist current governed memory and erase obsolete content."""

    def __init__(
        self,
        connection: sqlcipher.Connection,
        auditor: MemoryAuditor,
        lifecycle: MemoryLifecycle,
    ) -> None:
        """Bind encrypted persistence, audit, and retention services."""
        self._connection = connection
        self._auditor = auditor
        self._lifecycle = lifecycle

    def _read(self, memory_id: RecordId) -> MemoryRecord:
        row = self._connection.execute(
            "SELECT content_json FROM governed_memories WHERE memory_id=?", (str(memory_id),)
        ).fetchone()
        if row is None:
            raise MemoryNotFoundError(memory_id=memory_id)
        return MemoryRecord.model_validate_json(str(row[0]))

    def _insert(self, record: MemoryRecord) -> None:
        _ = self._connection.execute(
            "INSERT INTO governed_memories VALUES(?, ?, ?, ?, ?)",
            (
                str(record.memory_id),
                record.revision,
                None if record.retain_until is None else record.retain_until.isoformat(),
                record.model_dump_json(),
                record.created_at.isoformat(),
            ),
        )

    def remember(self, record: MemoryRecord, context: TransitionContext) -> None:
        """Persist a new governed memory and provenance audit."""
        self._auditor.verify()
        self._insert(record)
        self._auditor.append(record, "created", self._auditor.fingerprint(record), context)
        self._connection.commit()

    def retrieve(self, scope: RetrievalScope) -> tuple[MemoryRecord, ...]:
        """Return only current, unexpired memories authorized for a context."""
        rows = self._connection.execute(
            "SELECT content_json FROM governed_memories ORDER BY created_at, memory_id"
        ).fetchall()
        now = self._lifecycle.now()
        records = tuple(MemoryRecord.model_validate_json(str(row[0])) for row in rows)
        return tuple(
            record
            for record in records
            if scope in record.retrieval_scopes
            and (record.retain_until is None or record.retain_until > now)
        )

    def correct(
        self,
        memory_id: RecordId,
        correction: MemoryCorrection,
        context: TransitionContext,
    ) -> MemoryRecord:
        """Replace a current revision while retaining only its keyed fingerprint."""
        self._auditor.verify()
        current = self._read(memory_id)
        self._require_revision(current, correction.expected_revision)
        fingerprint = self._auditor.fingerprint(current)
        replacement = MemoryRecord(
            memory_id=memory_id,
            category=current.category,
            content=correction.content,
            provenance=correction.provenance,
            confidence=correction.confidence,
            retrieval_scopes=correction.retrieval_scopes,
            created_at=current.created_at,
            retain_until=correction.retain_until,
            revision=current.revision + 1,
        )
        self._remove(current, MemoryRemovalReason.USER_CORRECTION, fingerprint, context)
        self._insert(replacement)
        self._auditor.append(current, "corrected", fingerprint, context)
        self._connection.commit()
        return replacement

    def delete(
        self,
        memory_id: RecordId,
        deletion: MemoryDeletion,
        context: TransitionContext,
    ) -> None:
        """Erase a user-selected memory revision and retain a keyed fingerprint."""
        self._auditor.verify()
        current = self._read(memory_id)
        self._require_revision(current, deletion.expected_revision)
        fingerprint = self._auditor.fingerprint(current)
        self._remove(current, MemoryRemovalReason.USER_DELETION, fingerprint, context)
        self._auditor.append(current, "deleted", fingerprint, context)
        self._connection.commit()

    def purge_expired(self, context: TransitionContext) -> int:
        """Erase all retention-expired content and retain keyed fingerprints."""
        self._auditor.verify()
        query = (
            "SELECT content_json FROM governed_memories "
            "WHERE retain_until IS NOT NULL AND retain_until<=?"
        )
        rows = self._connection.execute(query, (self._lifecycle.now().isoformat(),)).fetchall()
        expired = tuple(MemoryRecord.model_validate_json(str(row[0])) for row in rows)
        for record in expired:
            fingerprint = self._auditor.fingerprint(record)
            self._remove(record, MemoryRemovalReason.RETENTION_EXPIRED, fingerprint, context)
            self._auditor.append(record, "retention_expired", fingerprint, context)
        self._connection.commit()
        return len(expired)

    def _require_revision(self, current: MemoryRecord, expected: int) -> None:
        if expected != current.revision:
            raise StaleMemoryRevisionError(
                memory_id=current.memory_id,
                expected=expected,
                current=current.revision,
            )

    def _remove(
        self,
        record: MemoryRecord,
        reason: MemoryRemovalReason,
        fingerprint: str,
        context: TransitionContext,
    ) -> None:
        with self._lifecycle.deletion():
            _ = self._connection.execute(
                "DELETE FROM governed_memories WHERE memory_id=?", (str(record.memory_id),)
            )
        _ = self._connection.execute(
            "INSERT INTO memory_tombstones VALUES(?, ?, ?, ?, ?)",
            (
                str(record.memory_id),
                record.revision,
                context.occurred_at.isoformat(),
                reason.value,
                fingerprint,
            ),
        )

    def tombstones(self, memory_id: RecordId) -> tuple[MemoryTombstone, ...]:
        """Return content-free removal evidence for a memory's revisions."""
        query = (
            "SELECT revision, removed_at, reason, source_fingerprint "
            "FROM memory_tombstones WHERE memory_id=? ORDER BY revision"
        )
        rows = self._connection.execute(query, (str(memory_id),)).fetchall()
        return tuple(
            MemoryTombstone(
                memory_id=memory_id,
                revision=int(row[0]),
                removed_at=datetime.fromisoformat(str(row[1])),
                reason=MemoryRemovalReason(str(row[2])),
                source_fingerprint=str(row[3]),
            )
            for row in rows
        )
