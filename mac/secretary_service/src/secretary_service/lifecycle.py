"""Retention expiry and deletion orchestration for encrypted records."""

from datetime import timedelta
from typing import final

from secretary_service.backups import BackupManager
from secretary_service.models import (
    BackupId,
    FrozenModel,
    RecordId,
    RecordKind,
    TransitionContext,
    record_kind,
    source_reference,
)
from secretary_service.storage import Clock, EncryptedStateStore

COMMUNICATION_RETENTION = timedelta(days=180)
HEALTH_RETENTION = timedelta(days=365)
AUDIT_RETENTION = timedelta(days=730)


class ExpiryReport(FrozenModel):
    """Content-free counts of expired records by kind."""

    expired_by_kind: dict[RecordKind, int]
    expired_audit_entries: int
    expired_backups: int


class DeletionResult(FrozenModel):
    """Backup invalidation and clean replacement result."""

    invalidated_backup_ids: tuple[BackupId, ...]
    replacement_backup_id: BackupId


@final
class LifecycleManager:
    """Apply retention and user deletion across live and backup state."""

    def __init__(
        self,
        store: EncryptedStateStore,
        backups: BackupManager,
        clock: Clock,
        communication_retention: timedelta = COMMUNICATION_RETENTION,
        health_retention: timedelta = HEALTH_RETENTION,
    ) -> None:
        self._store = store
        self._backups = backups
        self._clock = clock
        self._communication_retention = communication_retention
        self._health_retention = health_retention

    def run_expiry(self, context: TransitionContext) -> ExpiryReport:
        """Expire raw records whose configured deadline has passed."""
        counts: dict[RecordKind, int] = {}
        for record in self._store.records():
            kind = record_kind(record)
            retention = self._retention(kind)
            if retention is not None and record.created_at + retention <= self._clock.now():
                _ = self._backups.invalidate_containing(kind, record.record_id)
                self._store.delete_record(kind, record.record_id, context)
                counts[kind] = counts.get(kind, 0) + 1
        expired_audit_entries = self._store.prune_audit_before(self._clock.now() - AUDIT_RETENTION)
        expired_backups = len(self._backups.expire().expired_backup_ids)
        return ExpiryReport(
            expired_by_kind=counts,
            expired_audit_entries=expired_audit_entries,
            expired_backups=expired_backups,
        )

    def _retention(self, kind: RecordKind) -> timedelta | None:
        configured = {
            RecordKind.SOURCE_ITEM: self._communication_retention,
            RecordKind.ENERGY_CHECK_IN: self._health_retention,
        }
        return configured.get(kind)

    def delete(
        self,
        kind: RecordKind,
        record_id: RecordId,
        context: TransitionContext,
    ) -> DeletionResult:
        """Purge a record, invalidate history, and create a clean backup."""
        invalidated = list(self._backups.invalidate_containing(kind, record_id))
        related = tuple(
            record for record in self._store.records() if source_reference(record) == record_id
        )
        for record in related:
            related_kind = record_kind(record)
            invalidated.extend(self._backups.invalidate_containing(related_kind, record.record_id))
            self._store.delete_record(related_kind, record.record_id, context)
        self._store.delete_record(kind, record_id, context)
        replacement = self._backups.create(context)
        return DeletionResult(
            invalidated_backup_ids=tuple(dict.fromkeys(invalidated)),
            replacement_backup_id=replacement.backup_id,
        )
