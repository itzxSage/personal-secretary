"""Device enrollment, revocation, and mTLS identity verification.

A device proves its identity by presenting an mTLS client certificate whose
public-key fingerprint matches an enrolled, active device. Voice and channel
surfaces never carry device identity; only an enrolled device can produce a
device-signed approval proof.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, ClassVar, NewType, final, override

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import ConfigDict, StringConstraints

from secretary_service.device_persistence import DevicePersistence
from secretary_service.models import ActorId, FrozenModel, NonEmpty
from secretary_service.storage import Clock

DeviceId = NewType("DeviceId", str)


class DeviceState(StrEnum):
    """Closed device lifecycle states."""

    ENROLLED = "enrolled"
    REVOKED = "revoked"


class Device(FrozenModel):
    """Enrolled device identity with audit attribution."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    device_id: DeviceId
    public_key_fingerprint: NonEmpty
    enrolled_at: datetime
    enrolled_by: Annotated[ActorId, StringConstraints(min_length=1)]
    state: DeviceState
    revoked_at: datetime | None = None
    revoked_by: ActorId | None = None
    approval_public_key: str | None = None


@final
class ForgedDeviceError(Exception):
    """Presented mTLS identity matches no enrolled active device."""

    def __init__(self, fingerprint: str) -> None:
        super().__init__(fingerprint)
        self.fingerprint = fingerprint

    @override
    def __str__(self) -> str:
        return f"no enrolled active device matches public key fingerprint {self.fingerprint}"


@final
class RevokedDeviceError(Exception):
    """Presented mTLS identity belongs to a revoked device."""

    def __init__(self, device_id: DeviceId) -> None:
        super().__init__(device_id)
        self.device_id = device_id

    @override
    def __str__(self) -> str:
        return f"device {self.device_id} is revoked"


@final
class DeviceNotFoundError(Exception):
    """Requested device is not enrolled."""

    def __init__(self, device_id: DeviceId) -> None:
        super().__init__(device_id)
        self.device_id = device_id

    @override
    def __str__(self) -> str:
        return f"device {self.device_id} is not enrolled"


@final
class DeviceAlreadyEnrolledError(Exception):
    """Device id or public key fingerprint is already enrolled."""

    def __init__(self, device_id: DeviceId) -> None:
        super().__init__(device_id)
        self.device_id = device_id

    @override
    def __str__(self) -> str:
        return f"device {self.device_id} is already enrolled"


@final
class DeviceRegistry:
    """Device enrollment and mTLS identity checks with optional encrypted persistence."""

    def __init__(self, clock: Clock, persistence: DevicePersistence | None = None) -> None:
        self._clock = clock
        self._persistence = persistence
        self._devices: dict[DeviceId, Device] = {}
        self._by_fingerprint: dict[str, DeviceId] = {}

    def enroll(
        self,
        device_id: DeviceId,
        public_key_fingerprint: str,
        actor: ActorId,
        *,
        approval_public_key: str | None = None,
    ) -> Device:
        """Enroll one device under its public key fingerprint with an audit actor."""
        if self.device(device_id) is not None:
            raise DeviceAlreadyEnrolledError(device_id)
        if public_key_fingerprint in self._by_fingerprint:
            raise DeviceAlreadyEnrolledError(self._by_fingerprint[public_key_fingerprint])
        if approval_public_key is not None:
            _ = Ed25519PublicKey.from_public_bytes(bytes.fromhex(approval_public_key))
        device = Device(
            device_id=device_id,
            public_key_fingerprint=public_key_fingerprint,
            enrolled_at=self._clock.now(),
            enrolled_by=actor,
            state=DeviceState.ENROLLED,
            approval_public_key=approval_public_key,
        )
        if self._persistence is not None and not self._persistence.insert(
            device_id, public_key_fingerprint, device.model_dump_json()
        ):
            raise DeviceAlreadyEnrolledError(device_id)
        self._devices[device_id] = device
        self._by_fingerprint[public_key_fingerprint] = device_id
        return device

    def revoke(self, device_id: DeviceId, actor: ActorId) -> Device:
        """Revoke one device; its identity fails closed from now on."""
        device = self.device(device_id)
        if device is None:
            raise DeviceNotFoundError(device_id)
        revoked = device.model_copy(
            update={
                "state": DeviceState.REVOKED,
                "revoked_at": self._clock.now(),
                "revoked_by": actor,
            }
        )
        self._devices[device_id] = revoked
        if self._persistence is not None:
            self._persistence.revoke(device_id, revoked.model_dump_json())
        return revoked

    def verify_mtls_identity(self, device_id: DeviceId, public_key_fingerprint: str) -> Device:
        """Accept only enrolled, active devices whose fingerprint matches."""
        device = self.device(device_id)
        if device is None or device.public_key_fingerprint != public_key_fingerprint:
            raise ForgedDeviceError(public_key_fingerprint)
        if device.state != DeviceState.ENROLLED:
            raise RevokedDeviceError(device_id)
        return device

    def device(self, device_id: DeviceId) -> Device | None:
        """Return the current device record, if enrolled."""
        if self._persistence is not None:
            record = self._persistence.load(device_id)
            return Device.model_validate_json(record) if record is not None else None
        return self._devices.get(device_id)

    def require_active_device(self, device_id: DeviceId) -> Device:
        """Return an enrolled device or fail closed for approval authorization."""
        device = self.device(device_id)
        if device is None:
            raise DeviceNotFoundError(device_id)
        if device.state != DeviceState.ENROLLED:
            raise RevokedDeviceError(device_id)
        return device
