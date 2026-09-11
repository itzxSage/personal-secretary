"""Durable catalog for encrypted backup envelopes."""

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Final, final, override
from uuid import UUID

from sqlcipher3 import dbapi2 as sqlcipher

from secretary_service.models import (
    BackupId,
    BackupManifest,
    BackupStatus,
    DomainRecord,
    RecordId,
    RecordKind,
    record_kind,
)

MANIFEST_QUERY: Final = "SELECT path, created_at, expires_at, COALESCE(wrapped_data_key, X''), COALESCE(nonce, X''), status FROM backup_manifests WHERE backup_id=?"  # noqa: E501


@final
class BackupCatalogError(Exception):
    """Persisted backup envelope metadata is inconsistent."""

    def __init__(self, backup_id: BackupId | None) -> None:
        super().__init__(backup_id)
        self.backup_id = backup_id

    @override
    def __str__(self) -> str:
        return "backup catalog metadata is inconsistent"


@final
class BackupCatalog:
    """Persist manifests and record membership without backup key material."""

    def __init__(self, connection: sqlcipher.Connection) -> None:
        self._connection = connection

    def register(self, manifest: BackupManifest, records: Iterable[DomainRecord]) -> None:
        """Persist one envelope and an index of records it contains."""
        _ = self._connection.execute(
            "INSERT INTO backup_manifests VALUES(?, ?, ?, ?, ?, ?, ?)",
            (
                str(manifest.backup_id),
                str(manifest.path),
                manifest.created_at.isoformat(),
                manifest.expires_at.isoformat(),
                manifest.wrapped_data_key,
                manifest.nonce,
                manifest.status.value,
            ),
        )
        _ = self._connection.executemany(
            "INSERT INTO backup_records VALUES(?, ?, ?)",
            (
                (str(manifest.backup_id), record_kind(record).value, str(record.record_id))
                for record in records
            ),
        )
        self._connection.commit()

    def manifest(self, backup_id: BackupId) -> BackupManifest:
        """Read one backup manifest, including destroyed-envelope state."""
        row = self._connection.execute(MANIFEST_QUERY, (str(backup_id),)).fetchone()
        if row is None:
            raise LookupError(str(backup_id))
        wrapped_data_key = self._optional_blob(row[3])
        nonce = self._optional_blob(row[4])
        if (wrapped_data_key is None) != (nonce is None):
            raise BackupCatalogError(backup_id=backup_id)
        return BackupManifest(
            backup_id=backup_id,
            path=Path(str(row[0])),
            created_at=datetime.fromisoformat(str(row[1])),
            expires_at=datetime.fromisoformat(str(row[2])),
            wrapped_data_key=wrapped_data_key,
            nonce=nonce,
            status=BackupStatus(str(row[5])),
        )

    @staticmethod
    def _optional_blob(value: str | bytes) -> bytes | None:
        if value == b"":
            return None
        if isinstance(value, bytes):
            return value
        raise BackupCatalogError(backup_id=None)

    def manifests(self) -> tuple[BackupManifest, ...]:
        """Read all durable backup manifests."""
        rows = self._connection.execute("SELECT backup_id FROM backup_manifests").fetchall()
        return tuple(self.manifest(BackupId(UUID(str(row[0])))) for row in rows)

    def containing(self, kind: RecordKind, record_id: RecordId) -> tuple[BackupId, ...]:
        """Find backup sets containing one record."""
        rows = self._connection.execute(
            "SELECT backup_id FROM backup_records WHERE record_kind=? AND record_id=?",
            (kind.value, str(record_id)),
        ).fetchall()
        return tuple(BackupId(UUID(str(row[0]))) for row in rows)

    def destroy_key(self, backup_id: BackupId, status: BackupStatus) -> None:
        """Erase one wrapped data key and nonce."""
        _ = self._connection.execute(
            "UPDATE backup_manifests SET wrapped_data_key=NULL, nonce=NULL, status=? WHERE backup_id=?",  # noqa: E501
            (status.value, str(backup_id)),
        )
        self._connection.commit()
