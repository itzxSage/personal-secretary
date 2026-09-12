"""Encrypted device membership checked under the same lock as approval consumption."""

from collections.abc import Callable
from typing import TYPE_CHECKING, final
from uuid import uuid5

import psycopg

from secretary_service.cloud_crypto import CloudCipher
from secretary_service.enrollment import Device, DeviceState
from secretary_service.models import (
    CorrelationId,
    Identity,
    RecordId,
    RecordKind,
    TransitionContext,
)

if TYPE_CHECKING:
    from secretary_service.persistence import DomainRecords
    from secretary_service.postgres_types import PgConnection


@final
class PostgresDevicePersistence:
    """Persist enrollment and irreversible revocation within an execution unit."""

    def __init__(
        self,
        connection: "PgConnection",
        cipher: CloudCipher,
        records: "DomainRecords",
        require_active: Callable[[], None],
    ) -> None:
        """Bind device reads and mutations to the owning tenant transaction."""
        self._connection = connection
        self._cipher = cipher
        self._records = records
        self._require_active = require_active

    def load(self, device_id: str) -> str | None:
        """Read current state on every authorization check, authenticating revocation."""
        self._require_active()
        row = self._connection.execute(
            "SELECT fingerprint, revoked::text, envelope FROM lifeos_devices WHERE device_id=%s",
            (device_id,),
        ).fetchone()
        if row is None:
            return None
        encoded = self._cipher.open(row[2], ("device", device_id, row[1]))
        device = Device.model_validate_json(encoded)
        revoked = device.state == DeviceState.REVOKED
        if (
            str(device.device_id) != device_id
            or device.public_key_fingerprint != row[0]
            or revoked != (row[1] == "true")
        ):
            message = "device membership integrity mismatch"
            raise ValueError(message)
        return encoded

    def _record_id(self, device_id: str) -> RecordId:
        return RecordId(uuid5(self._cipher.tenant_id, "device:" + device_id))

    def insert(self, device_id: str, fingerprint: str, record: str) -> bool:
        """Reject duplicate device IDs/fingerprints; include enrollment in audit."""
        self._require_active()
        device = Device.model_validate_json(record)
        if (
            device.device_id != device_id
            or device.public_key_fingerprint != fingerprint
            or device.state != DeviceState.ENROLLED
        ):
            message = "invalid enrollment record"
            raise ValueError(message)
        try:
            with self._connection.transaction():
                _ = self._connection.execute(
                    "INSERT INTO lifeos_devices VALUES (%s, %s, false, %s)",
                    (
                        device_id,
                        fingerprint,
                        self._cipher.seal(record, ("device", device_id, "false")),
                    ),
                )
                self._records.create(
                    Identity(
                        record_id=self._record_id(device_id),
                        created_at=device.enrolled_at,
                        state="enrolled",
                        display_name="enrolled-device",
                    ),
                    TransitionContext(
                        actor=device.enrolled_by,
                        correlation_id=CorrelationId("device-enrollment"),
                        occurred_at=device.enrolled_at,
                    ),
                )
        except psycopg.errors.UniqueViolation:
            return False
        return True

    def revoke(self, device_id: str, record: str) -> None:
        """Irreversibly revoke without changing device keys or enrollment attribution."""
        self._require_active()
        original = self.load(device_id)
        changed = Device.model_validate_json(record)
        if original is None:
            message = "cannot revoke unknown device"
            raise ValueError(message)
        current = Device.model_validate_json(original)
        fields = {"state", "revoked_at", "revoked_by"}
        if (
            changed.state != DeviceState.REVOKED
            or changed.revoked_at is None
            or changed.revoked_by is None
            or current.model_dump(exclude=fields) != changed.model_dump(exclude=fields)
        ):
            message = "revocation cannot change enrolled identity"
            raise ValueError(message)
        if current.state == DeviceState.REVOKED:
            return
        with self._connection.transaction():
            _ = self._connection.execute(
                "UPDATE lifeos_devices SET revoked=true, envelope=%s WHERE device_id=%s",
                (self._cipher.seal(record, ("device", device_id, "true")), device_id),
            )
            self._records.transition(
                RecordKind.IDENTITY,
                self._record_id(device_id),
                "revoked",
                TransitionContext(
                    actor=changed.revoked_by,
                    correlation_id=CorrelationId("device-revocation"),
                    occurred_at=changed.revoked_at,
                ),
            )
