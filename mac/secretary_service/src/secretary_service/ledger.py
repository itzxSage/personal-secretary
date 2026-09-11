"""SQLCipher-backed append-only audit ledger."""

import hmac
import json
from collections.abc import Callable
from datetime import datetime
from typing import final
from uuid import UUID

from sqlcipher3 import dbapi2 as sqlcipher

from secretary_service.audit import (
    AuditEntry,
    AuditMutation,
    AuditTamperError,
    AuditVerification,
    sign_entry,
)
from secretary_service.keys import KeyProvider
from secretary_service.models import ActorId, CorrelationId, RecordId, RecordKind


@final
class AuditLedger:
    """Append and verify content-minimized cryptographic audit records."""

    def __init__(self, connection: sqlcipher.Connection, keys: KeyProvider) -> None:
        self._connection = connection
        self._keys = keys

    def append(self, mutation: AuditMutation) -> None:
        """Append one HMAC-chained immutable audit record."""
        row = self._connection.execute(
            "SELECT sequence, entry_hash FROM audit_entries ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        if row is None:
            row = self._connection.execute(
                "SELECT sequence, entry_hash FROM audit_chain_anchor WHERE singleton=1"
            ).fetchone()
        sequence = 1 if row is None else int(row[0]) + 1
        previous_hash = "0" * 64 if row is None else str(row[1])
        unsigned = AuditEntry(
            sequence=sequence,
            occurred_at=mutation.context.occurred_at,
            action_class=mutation.action_class,
            record_kind=mutation.record_kind,
            record_id=mutation.record_id,
            actor=mutation.context.actor,
            correlation_id=mutation.context.correlation_id,
            source_fingerprint=mutation.source_fingerprint,
            action_metadata=json.dumps(
                {"state": mutation.state}, separators=(",", ":"), sort_keys=True
            ),
            previous_hash=previous_hash,
            entry_hash="",
        )
        entry = unsigned.model_copy(
            update={"entry_hash": sign_entry(unsigned, self._keys.audit_key())}
        )
        _ = self._connection.execute(
            "INSERT INTO audit_entries VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry.sequence,
                entry.occurred_at.isoformat(),
                entry.action_class,
                entry.record_kind.value,
                str(entry.record_id),
                entry.actor,
                entry.correlation_id,
                entry.source_fingerprint,
                entry.action_metadata,
                entry.previous_hash,
                entry.entry_hash,
            ),
        )

    def entries(self) -> tuple[AuditEntry, ...]:
        """Return all retained entries in sequence order."""
        rows = self._connection.execute("SELECT * FROM audit_entries ORDER BY sequence").fetchall()
        return tuple(
            AuditEntry(
                sequence=int(row[0]),
                occurred_at=datetime.fromisoformat(str(row[1])),
                action_class=str(row[2]),
                record_kind=RecordKind(str(row[3])),
                record_id=RecordId(UUID(str(row[4]))),
                actor=ActorId(str(row[5])),
                correlation_id=CorrelationId(str(row[6])),
                source_fingerprint=str(row[7]),
                action_metadata=str(row[8]),
                previous_hash=str(row[9]),
                entry_hash=str(row[10]),
            )
            for row in rows
        )

    def verify(self) -> AuditVerification:
        """Verify retained sequence, predecessor links, and signatures."""
        anchor = self._connection.execute(
            "SELECT sequence, entry_hash FROM audit_chain_anchor WHERE singleton=1"
        ).fetchone()
        anchor_sequence = 0 if anchor is None else int(anchor[0])
        previous_hash = "0" * 64 if anchor is None else str(anchor[1])
        entries = self.entries()
        for expected_sequence, entry in enumerate(entries, start=anchor_sequence + 1):
            valid = (
                entry.sequence == expected_sequence
                and entry.previous_hash == previous_hash
                and hmac.compare_digest(entry.entry_hash, sign_entry(entry, self._keys.audit_key()))
            )
            if not valid:
                raise AuditTamperError(sequence=expected_sequence)
            previous_hash = entry.entry_hash
        return AuditVerification(entries_verified=len(entries), final_hash=previous_hash)

    def prune_before(
        self,
        cutoff: datetime,
        authorize_delete: Callable[[], None],
        revoke_delete: Callable[[], None],
    ) -> int:
        """Prune a verified prefix and persist its final chain hash as anchor."""
        _ = self.verify()
        expired = tuple(entry for entry in self.entries() if entry.occurred_at < cutoff)
        if not expired:
            return 0
        last = expired[-1]
        authorize_delete()
        try:
            _ = self._connection.execute(
                "DELETE FROM audit_entries WHERE sequence<=?", (last.sequence,)
            )
        finally:
            revoke_delete()
        _ = self._connection.execute(
            "UPDATE audit_chain_anchor SET sequence=?, entry_hash=? WHERE singleton=1",
            (last.sequence, last.entry_hash),
        )
        self._connection.commit()
        return len(expired)
