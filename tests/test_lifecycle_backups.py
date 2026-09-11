from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest

from secretary_service.backups import BackupInvalidatedError, BackupManager
from secretary_service.keys import DeterministicTestKeyProvider
from secretary_service.lifecycle import LifecycleManager
from secretary_service.models import (
    ActorId,
    BackupStatus,
    CorrelationId,
    EnergyCheckIn,
    NormalizedFact,
    RecordId,
    RecordKind,
    SourceItem,
    TransitionContext,
)
from secretary_service.storage import EncryptedStateStore
from tests.helpers import FakeClock


def context(clock: FakeClock, correlation_id: str) -> TransitionContext:
    return TransitionContext(
        actor=ActorId("user"),
        correlation_id=CorrelationId(correlation_id),
        occurred_at=clock.now(),
    )


def test_default_retention_expires_communications_at_180_and_health_at_365_days(
    store: EncryptedStateStore,
    tmp_path: Path,
    keys: DeterministicTestKeyProvider,
    clock: FakeClock,
) -> None:
    source_id = RecordId(UUID("00000000-0000-0000-0000-000000000101"))
    checkin_id = RecordId(UUID("00000000-0000-0000-0000-000000000102"))
    store.create(
        SourceItem(
            record_id=source_id,
            created_at=clock.now(),
            source_type="communication",
            external_id="retention-source",
            raw_content="expiring communication",
        ),
        context(clock, "corr-retention-source"),
    )
    store.create(
        EnergyCheckIn(
            record_id=checkin_id,
            created_at=clock.now(),
            energy=3,
            mood="fixture mood",
        ),
        context(clock, "corr-retention-health"),
    )
    backups = BackupManager(store, tmp_path / "backups", keys, clock)
    lifecycle = LifecycleManager(store, backups, clock)

    clock.advance(timedelta(days=180, seconds=1))
    first = lifecycle.run_expiry(context(clock, "corr-expiry-180"))
    assert first.expired_by_kind == {RecordKind.SOURCE_ITEM: 1}
    assert store.read(RecordKind.SOURCE_ITEM, source_id) is None
    assert store.read(RecordKind.ENERGY_CHECK_IN, checkin_id) is not None

    clock.advance(timedelta(days=185))
    second = lifecycle.run_expiry(context(clock, "corr-expiry-365"))
    assert second.expired_by_kind == {RecordKind.ENERGY_CHECK_IN: 1}
    assert store.read(RecordKind.ENERGY_CHECK_IN, checkin_id) is None


def test_user_deletion_destroys_historical_backup_key_and_creates_clean_backup(
    store: EncryptedStateStore,
    tmp_path: Path,
    keys: DeterministicTestKeyProvider,
    clock: FakeClock,
) -> None:
    source_id = RecordId(UUID("00000000-0000-0000-0000-000000000110"))
    raw_fixture = "historical-secret-fixture-e2d1"
    store.create(
        SourceItem(
            record_id=source_id,
            created_at=clock.now(),
            source_type="communication",
            external_id="delete-source",
            raw_content=raw_fixture,
        ),
        context(clock, "corr-before-delete"),
    )
    fact_id = RecordId(UUID("00000000-0000-0000-0000-000000000111"))
    store.create(
        NormalizedFact(
            record_id=fact_id,
            created_at=clock.now(),
            state="normalized",
            source_id=source_id,
            fact_type="fixture",
            value="derived sensitive fixture",
        ),
        context(clock, "corr-before-delete"),
    )
    backups = BackupManager(store, tmp_path / "backups", keys, clock)
    historical = backups.create(context(clock, "corr-backup"))
    assert backups.verify_restore(historical.backup_id).audit_entries >= 1

    lifecycle = LifecycleManager(store, backups, clock)
    result = lifecycle.delete(
        RecordKind.SOURCE_ITEM,
        source_id,
        context(clock, "corr-delete"),
    )

    assert backups.manifest(historical.backup_id).status is BackupStatus.INVALIDATED
    assert backups.manifest(historical.backup_id).wrapped_data_key is None
    with pytest.raises(BackupInvalidatedError):
        _ = backups.verify_restore(historical.backup_id)
    assert result.replacement_backup_id != historical.backup_id
    assert backups.verify_restore(result.replacement_backup_id).audit_entries >= 2
    assert store.read(RecordKind.SOURCE_ITEM, source_id) is None
    assert store.read(RecordKind.NORMALIZED_FACT, fact_id) is None
    assert raw_fixture not in store.tombstone(source_id).model_dump_json()
    assert raw_fixture.encode() not in historical.path.read_bytes()
    assert all(raw_fixture not in entry.model_dump_json() for entry in store.audit_entries())


def test_backup_expiry_destroys_wrapped_key_at_30_days(
    store: EncryptedStateStore,
    tmp_path: Path,
    keys: DeterministicTestKeyProvider,
    clock: FakeClock,
) -> None:
    backups = BackupManager(store, tmp_path / "backups", keys, clock)
    manifest = backups.create(context(clock, "corr-backup-expiry"))

    clock.advance(timedelta(days=30, seconds=1))
    report = backups.expire()

    assert report.expired_backup_ids == (manifest.backup_id,)
    assert backups.manifest(manifest.backup_id).status is BackupStatus.EXPIRED
    assert backups.manifest(manifest.backup_id).wrapped_data_key is None
    with pytest.raises(BackupInvalidatedError):
        _ = backups.verify_restore(manifest.backup_id)


def test_two_year_audit_retention_preserves_verifiable_chain_anchor(
    store: EncryptedStateStore,
    tmp_path: Path,
    keys: DeterministicTestKeyProvider,
    clock: FakeClock,
) -> None:
    source_id = RecordId(UUID("00000000-0000-0000-0000-000000000120"))
    store.create(
        SourceItem(
            record_id=source_id,
            created_at=clock.now(),
            source_type="communication",
            external_id="audit-retention",
            raw_content="synthetic retention fixture",
        ),
        context(clock, "corr-audit-retention"),
    )
    backups = BackupManager(store, tmp_path / "backups", keys, clock)
    lifecycle = LifecycleManager(store, backups, clock)

    clock.advance(timedelta(days=731))
    report = lifecycle.run_expiry(context(clock, "corr-audit-prune"))

    assert report.expired_audit_entries == 1
    assert [entry.sequence for entry in store.audit_entries()] == [2]
    assert store.verify_audit_chain().entries_verified == 1
