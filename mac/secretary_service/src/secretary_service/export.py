"""User-requested metadata-only export boundary."""

from typing import final
from uuid import NAMESPACE_URL, uuid5

from secretary_service.models import (
    FrozenModel,
    RecordId,
    RecordKind,
    TransitionContext,
    record_kind,
)
from secretary_service.storage import EncryptedStateStore


class RedactedRecord(FrozenModel):
    """Metadata-only record representation."""

    record_id: RecordId
    record_kind: RecordKind
    state: str
    content_redacted: bool


class RedactedExport(FrozenModel):
    """User-requested export containing no sensitive content."""

    records: tuple[RedactedRecord, ...]
    audit_entries: int


@final
class StateExporter:
    """Produce audited redacted exports from encrypted state."""

    def __init__(self, store: EncryptedStateStore) -> None:
        self._store = store

    def redacted(self, context: TransitionContext) -> RedactedExport:
        """Export identities and states while redacting all content."""
        records = tuple(
            RedactedRecord(
                record_id=record.record_id,
                record_kind=record_kind(record),
                state=record.state,
                content_redacted=True,
            )
            for record in self._store.records()
        )
        marker_id = (
            records[0].record_id
            if records
            else RecordId(uuid5(NAMESPACE_URL, str(context.correlation_id)))
        )
        self._store.append_export_audit(marker_id, context)
        return RedactedExport(records=records, audit_entries=len(self._store.audit_entries()))
