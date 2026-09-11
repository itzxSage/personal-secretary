import os
from pathlib import Path

import pytest

from secretary_service.backups import BackupManager
from secretary_service.keys import DeterministicTestKeyProvider
from secretary_service.models import Identity, RecordId, RecordKind
from secretary_service.recovery import BackupRecovery, RecoveryRequest
from secretary_service.storage import EncryptedStateStore, StoreKeyError
from tests.helpers import FakeClock
from tests.rollout_helpers import (
    RecoveryFixture,
    RotatedDatabaseKeyProvider,
    load_state_fixture,
    transition_context,
)

ROOT = Path(__file__).parents[1]


def test_backup_restores_after_database_key_loss_with_audit_preserved(
    tmp_path: Path,
    clock: FakeClock,
) -> None:
    recovery_fixture_path = Path(
        os.environ.get("LIFEOS_BACKUP_FIXTURE", ROOT / "fixtures/encrypted-backup")
    )
    expected = RecoveryFixture.model_validate_json(
        (recovery_fixture_path / "drill.json").read_text(encoding="utf-8")
    )
    fixture_path = Path(__file__).parents[1] / "fixtures/fresh-state"
    fixture = load_state_fixture(fixture_path)
    source_keys = DeterministicTestKeyProvider.from_seed(b"task-18-recovery")
    source_path = tmp_path / "source.sqlite"
    with EncryptedStateStore.open(source_path, source_keys, clock) as source:
        source.create(
            Identity(
                record_id=RecordId(fixture.identity_id),
                created_at=clock.now(),
                state="active",
                display_name=fixture.display_name,
            ),
            transition_context(clock, "task-18-backup"),
        )
        manifest = BackupManager(source, tmp_path / "backups", source_keys, clock).create(
            transition_context(clock, "task-18-backup")
        )

    replacement_keys = RotatedDatabaseKeyProvider(source_keys, b"r" * 32)
    with pytest.raises(StoreKeyError):
        _ = EncryptedStateStore.open(source_path, replacement_keys, clock)

    report = BackupRecovery(clock).restore(
        RecoveryRequest(
            manifest=manifest,
            source_keys=source_keys,
            replacement_keys=replacement_keys,
            target_path=tmp_path / "restored.sqlite",
        )
    )

    with EncryptedStateStore.open(report.path, replacement_keys, clock) as restored:
        identity = restored.read(RecordKind.IDENTITY, RecordId(fixture.identity_id))
        assert isinstance(identity, Identity)
        assert identity.display_name == fixture.display_name
        assert restored.verify_audit_chain().final_hash == report.final_audit_hash
        assert restored.verify_audit_chain().entries_verified == report.audit_entries
        assert report.database_key_rotated is expected.database_key_rotated
        assert expected.invalid_key_rejected is True
        assert expected.audit_chain_valid is True
        assert expected.production_capabilities_enabled is False
