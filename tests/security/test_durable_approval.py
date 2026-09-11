"""Replay markers survive encrypted-state reopen and failed claims are atomic."""

from pathlib import Path

import pytest

from secretary_service.authority import (
    PolicyViolationError,
    ProposalLifecycle,
    default_approval_matrix,
)
from secretary_service.enrollment import (
    DeviceAlreadyEnrolledError,
    DeviceId,
    DeviceRegistry,
    RevokedDeviceError,
)
from secretary_service.fixture_authority import FIXTURE_PUBLIC_KEY
from secretary_service.keys import DeterministicTestKeyProvider
from secretary_service.storage import EncryptedStateStore
from tests.helpers import FakeClock
from tests.test_authority import ACTOR, DEFAULT_DEVICE_ID, make_approval, make_proposal


def test_approval_cannot_be_replayed_after_reopening_encrypted_state(
    tmp_path: Path,
    clock: FakeClock,
) -> None:
    path = tmp_path / "state.sqlite"
    keys = DeterministicTestKeyProvider.from_seed(b"durable-approval-test")
    devices = DeviceRegistry(clock)
    _ = devices.enroll(
        DEFAULT_DEVICE_ID, "fingerprint", ACTOR, approval_public_key=FIXTURE_PUBLIC_KEY
    )
    proposal = make_proposal(clock, "calendar.apply", "payload")
    approval = make_approval(clock, proposal)
    with EncryptedStateStore.open(path, keys, clock) as store:
        lifecycle = ProposalLifecycle(clock, devices, store.authority_consumption)
        _ = lifecycle.approve(proposal, approval, default_approval_matrix())
    with EncryptedStateStore.open(path, keys, clock) as store:
        lifecycle = ProposalLifecycle(clock, devices, store.authority_consumption)
        with pytest.raises(PolicyViolationError, match="replay"):
            _ = lifecycle.approve(proposal, approval, default_approval_matrix())
    assert str(approval.fact_id).encode() not in path.read_bytes()


def test_failed_multi_key_claim_rolls_back_new_markers(store: EncryptedStateStore) -> None:
    used = ("approve", "already-used")
    fresh = ("approve", "fresh")
    assert store.authority_consumption.consume((used,))
    assert not store.authority_consumption.consume((fresh, used))
    assert store.authority_consumption.consume((fresh,))


def test_device_enrollment_and_revocation_survive_restarts(
    tmp_path: Path, clock: FakeClock
) -> None:
    path = tmp_path / "devices.sqlite"
    keys = DeterministicTestKeyProvider.from_seed(b"durable-device-test")
    with EncryptedStateStore.open(path, keys, clock) as store:
        devices = DeviceRegistry(clock, store.devices)
        _ = devices.enroll(
            DEFAULT_DEVICE_ID, "fingerprint", ACTOR, approval_public_key=FIXTURE_PUBLIC_KEY
        )
    with EncryptedStateStore.open(path, keys, clock) as store:
        devices = DeviceRegistry(clock, store.devices)
        record = devices.require_active_device(DEFAULT_DEVICE_ID)
        assert record.approval_public_key == FIXTURE_PUBLIC_KEY
        with pytest.raises(DeviceAlreadyEnrolledError):
            _ = devices.enroll(DeviceId("new-id"), "fingerprint", ACTOR)
        another_registry = DeviceRegistry(clock, store.devices)
        _ = devices.revoke(DEFAULT_DEVICE_ID, ACTOR)
        with pytest.raises(RevokedDeviceError):
            _ = another_registry.require_active_device(DEFAULT_DEVICE_ID)
    with EncryptedStateStore.open(path, keys, clock) as store:
        devices = DeviceRegistry(clock, store.devices)
        with pytest.raises(RevokedDeviceError):
            _ = devices.verify_mtls_identity(DEFAULT_DEVICE_ID, "fingerprint")
    assert b"fingerprint" not in path.read_bytes()
