"""Encrypted persistence for device enrollment and irreversible revocation."""

from typing import Protocol, final

from sqlcipher3 import dbapi2 as sqlcipher


class DevicePersistence(Protocol):
    """Persist encoded device records without depending on their policy models."""

    def load(self, device_id: str) -> str | None:
        """Read the current device record for every authorization check."""
        ...

    def insert(self, device_id: str, fingerprint: str, record: str) -> bool:
        """Reject duplicate identities or fingerprints, including concurrent inserts."""
        ...

    def revoke(self, device_id: str, record: str) -> None:
        """Replace the device's state with its revoked record."""
        ...


@final
class EncryptedDevicePersistence:
    """Keep device public keys and revocation state in the owned SQLCipher store."""

    def __init__(self, connection: sqlcipher.Connection) -> None:
        """Use the owning state store's encrypted connection."""
        self._connection = connection

    def load(self, device_id: str) -> str | None:
        """Return the latest durable record; do not cache authorization state."""
        row = self._connection.execute(
            "SELECT record FROM enrolled_devices WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        return str(row[0]) if row else None

    def insert(self, device_id: str, fingerprint: str, record: str) -> bool:
        """Uniqueness is enforced by SQL, not just by an in-process dictionary."""
        try:
            with self._connection:
                _ = self._connection.execute(
                    "INSERT INTO enrolled_devices(device_id, fingerprint, record) VALUES (?, ?, ?)",
                    (device_id, fingerprint, record),
                )
        except sqlcipher.IntegrityError:
            return False
        return True

    def revoke(self, device_id: str, record: str) -> None:
        """Persist revocation before returning to the caller."""
        with self._connection:
            _ = self._connection.execute(
                "UPDATE enrolled_devices SET record = ? WHERE device_id = ?",
                (record, device_id),
            )
