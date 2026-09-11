from datetime import UTC, datetime, timedelta

import pytest

from secretary_service.voice import (
    KeychainRootKeyHandle,
    KeyRotationCoordinator,
    KeyRotationPlan,
    KeyRotationReason,
    OfflineRecoveryEnvelope,
    RecoveryEnvelopeError,
)
from tests.voice_test_support import RecordingKeychainCustodian

NOW: datetime = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)


def test_key_rotation_requires_verified_offline_recovery_envelope() -> None:
    custodian = RecordingKeychainCustodian([])
    coordinator = KeyRotationCoordinator(custodian)
    current = KeychainRootKeyHandle(
        reference="database-key-generation-7",
        generation=7,
        activated_at=NOW - timedelta(days=90),
    )
    invalid = OfflineRecoveryEnvelope(
        reference="offline-envelope-8",
        created_at=NOW,
        offline=False,
        sealed=True,
        audit_chain_verified=True,
    )

    with pytest.raises(RecoveryEnvelopeError):
        _ = coordinator.rotate(
            current,
            KeyRotationPlan(
                replacement_reference="database-key-generation-8",
                envelope=invalid,
                reason=KeyRotationReason.SCHEDULED,
                now=NOW,
            ),
        )

    assert custodian.rotations == []


def test_key_rotates_at_90_days_and_immediately_on_compromise() -> None:
    custodian = RecordingKeychainCustodian([])
    coordinator = KeyRotationCoordinator(custodian)
    envelope = OfflineRecoveryEnvelope(
        reference="offline-envelope-8",
        created_at=NOW,
        offline=True,
        sealed=True,
        audit_chain_verified=True,
    )
    current = KeychainRootKeyHandle(
        reference="database-key-generation-7",
        generation=7,
        activated_at=NOW - timedelta(days=90),
    )

    scheduled = coordinator.rotate(
        current,
        KeyRotationPlan(
            replacement_reference="database-key-generation-8",
            envelope=envelope,
            reason=KeyRotationReason.SCHEDULED,
            now=NOW,
        ),
    )
    compromised = coordinator.rotate(
        scheduled,
        KeyRotationPlan(
            replacement_reference="database-key-generation-9",
            envelope=envelope.model_copy(update={"reference": "offline-envelope-9"}),
            reason=KeyRotationReason.COMPROMISE,
            now=NOW + timedelta(seconds=1),
        ),
    )

    assert scheduled.generation == 8
    assert compromised.generation == 9
    assert custodian.rotations == [
        ("database-key-generation-7", "database-key-generation-8", "offline-envelope-8"),
        ("database-key-generation-8", "database-key-generation-9", "offline-envelope-9"),
    ]
