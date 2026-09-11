"""Encrypted backup envelopes and cryptographic invalidation."""

import secrets
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import final, override
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from secretary_service.keys import KeyProvider, KeyUnavailableError
from secretary_service.models import (
    BackupId,
    BackupManifest,
    BackupStatus,
    FrozenModel,
    RecordId,
    RecordKind,
    TransitionContext,
)
from secretary_service.storage import Clock, EncryptedStateStore, StoreKeyError

BACKUP_RETENTION = timedelta(days=30)


@final
class BackupInvalidatedError(Exception):
    """Backup cannot be restored because its envelope key is gone."""

    def __init__(self, backup_id: BackupId) -> None:
        super().__init__(backup_id)
        self.backup_id = backup_id

    @override
    def __str__(self) -> str:
        return f"backup key is unavailable: {self.backup_id}"


class RecoveryProof(FrozenModel):
    """Content-free evidence that a backup decrypted and verified."""

    backup_id: BackupId
    audit_entries: int
    audit_chain_valid: bool


class BackupExpiryReport(FrozenModel):
    """Backup identifiers whose wrapped keys were destroyed."""

    expired_backup_ids: tuple[BackupId, ...]


@dataclass(frozen=True, slots=True)
class _BackupKeyProvider:
    data_key: bytes
    source: KeyProvider

    def database_key(self) -> bytes:
        return self.data_key

    def audit_key(self) -> bytes:
        return self.source.audit_key()

    def backup_wrapping_key(self) -> bytes:
        return self.source.backup_wrapping_key()

    def connector_secret(self, reference: str) -> str:
        raise KeyUnavailableError(reference=reference)


@final
class BackupManager:
    """Create, verify, expire, and invalidate encrypted backup sets."""

    def __init__(
        self,
        store: EncryptedStateStore,
        directory: Path,
        keys: KeyProvider,
        clock: Clock,
    ) -> None:
        self._store = store
        self._directory = directory
        self._keys = keys
        self._clock = clock

    def create(self, context: TransitionContext) -> BackupManifest:
        """Create an encrypted backup under a unique envelope data key."""
        _ = self._store.verify_audit_chain()
        self._directory.mkdir(parents=True, exist_ok=True)
        backup_id = BackupId(uuid4())
        path = self._directory / f"{backup_id}.sqlite"
        data_key = secrets.token_bytes(32)
        nonce = secrets.token_bytes(12)
        wrapped = AESGCM(self._keys.backup_wrapping_key()).encrypt(
            nonce, data_key, str(backup_id).encode()
        )
        self._store.write_encrypted_backup(path, data_key)
        manifest = BackupManifest(
            backup_id=backup_id,
            path=path,
            created_at=context.occurred_at,
            expires_at=context.occurred_at + BACKUP_RETENTION,
            wrapped_data_key=wrapped,
            nonce=nonce,
            status=BackupStatus.ACTIVE,
        )
        self._store.register_backup(manifest)
        return manifest

    def manifest(self, backup_id: BackupId) -> BackupManifest:
        """Read durable metadata for one backup."""
        return self._store.backup_manifest(backup_id)

    def verify_restore(self, backup_id: BackupId) -> RecoveryProof:
        """Decrypt and verify a backup without plaintext output."""
        manifest = self.manifest(backup_id)
        if (
            manifest.status is not BackupStatus.ACTIVE
            or manifest.wrapped_data_key is None
            or manifest.nonce is None
        ):
            raise BackupInvalidatedError(backup_id=backup_id)
        try:
            data_key = AESGCM(self._keys.backup_wrapping_key()).decrypt(
                manifest.nonce,
                manifest.wrapped_data_key,
                str(backup_id).encode(),
            )
            with EncryptedStateStore.open(
                manifest.path,
                _BackupKeyProvider(data_key=data_key, source=self._keys),
                self._clock,
            ) as restored:
                verification = restored.verify_audit_chain()
        except (InvalidTag, StoreKeyError) as error:
            raise BackupInvalidatedError(backup_id=backup_id) from error
        return RecoveryProof(
            backup_id=backup_id,
            audit_entries=verification.entries_verified,
            audit_chain_valid=True,
        )

    def invalidate_containing(self, kind: RecordKind, record_id: RecordId) -> tuple[BackupId, ...]:
        """Destroy every wrapped key for backups containing a record."""
        backup_ids = self._store.backup_ids_containing(kind, record_id)
        for backup_id in backup_ids:
            manifest = self.manifest(backup_id)
            if manifest.status is BackupStatus.ACTIVE:
                self._store.destroy_backup_key(backup_id, BackupStatus.INVALIDATED)
        return backup_ids

    def expire(self) -> BackupExpiryReport:
        """Destroy wrapped keys at the exact 30-day deadline."""
        expired: list[BackupId] = []
        for manifest in self._store.backup_manifests():
            if manifest.status is BackupStatus.ACTIVE and manifest.expires_at <= self._clock.now():
                self._store.destroy_backup_key(manifest.backup_id, BackupStatus.EXPIRED)
                expired.append(manifest.backup_id)
        return BackupExpiryReport(expired_backup_ids=tuple(expired))
