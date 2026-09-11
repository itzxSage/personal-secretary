"""Device enrollment, revocation, and mTLS identity verification."""

import pytest

from secretary_service.enrollment import (
    Device,
    DeviceAlreadyEnrolledError,
    DeviceId,
    DeviceNotFoundError,
    DeviceRegistry,
    DeviceState,
    ForgedDeviceError,
    RevokedDeviceError,
)
from secretary_service.models import ActorId
from tests.helpers import FakeClock

ACTOR = ActorId("user")


def test_enrolled_device_verifies_mtls_identity(clock: FakeClock) -> None:
    registry = DeviceRegistry(clock)
    device = registry.enroll(DeviceId("iphone-1"), "fp-abc", ACTOR)
    assert device.state == DeviceState.ENROLLED
    verified = registry.verify_mtls_identity(DeviceId("iphone-1"), "fp-abc")
    assert verified.device_id == DeviceId("iphone-1")


def test_forged_device_is_rejected(clock: FakeClock) -> None:
    registry = DeviceRegistry(clock)
    _ = registry.enroll(DeviceId("iphone-1"), "fp-abc", ACTOR)
    # An attacker presents a different key under a valid device id.
    with pytest.raises(ForgedDeviceError):
        _ = registry.verify_mtls_identity(DeviceId("iphone-1"), "fp-forged")
    # An attacker presents a valid key under an unknown device id.
    with pytest.raises(ForgedDeviceError):
        _ = registry.verify_mtls_identity(DeviceId("iphone-2"), "fp-abc")


def test_revoked_device_cannot_reconnect(clock: FakeClock) -> None:
    registry = DeviceRegistry(clock)
    _ = registry.enroll(DeviceId("iphone-1"), "fp-abc", ACTOR)
    revoked = registry.revoke(DeviceId("iphone-1"), ACTOR)
    assert revoked.state == DeviceState.REVOKED
    assert revoked.revoked_by == ACTOR
    assert revoked.revoked_at == clock.now()
    # The previously valid identity is rejected after revocation.
    with pytest.raises(RevokedDeviceError):
        _ = registry.verify_mtls_identity(DeviceId("iphone-1"), "fp-abc")


def test_revoking_unknown_device_fails(clock: FakeClock) -> None:
    registry = DeviceRegistry(clock)
    with pytest.raises(DeviceNotFoundError):
        _ = registry.revoke(DeviceId("iphone-9"), ACTOR)


def test_duplicate_enrollment_is_rejected(clock: FakeClock) -> None:
    registry = DeviceRegistry(clock)
    _ = registry.enroll(DeviceId("iphone-1"), "fp-abc", ACTOR)
    with pytest.raises(DeviceAlreadyEnrolledError):
        _ = registry.enroll(DeviceId("iphone-1"), "fp-abc", ACTOR)
    # A second device cannot claim the same public key fingerprint.
    with pytest.raises(DeviceAlreadyEnrolledError):
        _ = registry.enroll(DeviceId("iphone-2"), "fp-abc", ACTOR)


def test_revoked_device_cannot_produce_device_signed_approval(clock: FakeClock) -> None:
    registry = DeviceRegistry(clock)
    _ = registry.enroll(DeviceId("iphone-1"), "fp-abc", ACTOR)
    _ = registry.revoke(DeviceId("iphone-1"), ACTOR)
    # The mTLS boundary rejects the revoked device before any approval can be
    # attributed to it.
    with pytest.raises(RevokedDeviceError):
        _ = registry.verify_mtls_identity(DeviceId("iphone-1"), "fp-abc")


def test_enrollment_records_audit_actor(clock: FakeClock) -> None:
    registry = DeviceRegistry(clock)
    device = registry.enroll(DeviceId("iphone-1"), "fp-abc", ActorId("admin"))
    assert device.enrolled_by == ActorId("admin")
    assert device.enrolled_at == clock.now()
    assert isinstance(device, Device)
