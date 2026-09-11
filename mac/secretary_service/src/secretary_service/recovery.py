"""Verified SQLCipher backup restore under a replacement database key."""

from dataclasses import dataclass
from pathlib import Path
from typing import final, override
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from secretary_service.backups import BackupInvalidatedError
from secretary_service.keys import KeyProvider, KeyUnavailableError
from secretary_service.models import BackupId, BackupManifest, BackupStatus, FrozenModel
from secretary_service.storage import Clock, EncryptedStateStore, StoreKeyError


@dataclass(frozen=True, slots=True)
class RecoveryRequest:
    """Trusted recovery inputs assembled from the sealed backup inventory."""

    manifest: BackupManifest
    source_keys: KeyProvider
    replacement_keys: KeyProvider
    target_path: Path


class RecoveryReport(FrozenModel):
    """Content-free evidence emitted after atomic restore verification."""

    path: Path
    backup_id: BackupId
    audit_entries: int
    final_audit_hash: str
    database_key_rotated: bool


@final
class RecoveryTargetError(Exception):
    """Recovery target already exists and cannot be overwritten implicitly."""

    def __init__(self, path: Path) -> None:
        """Record the protected target path."""
        super().__init__(path)
        self.path = path

    @override
    def __str__(self) -> str:
        return f"recovery target already exists: {self.path}"


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
class BackupRecovery:
    """Restore an eligible backup without plaintext output or implicit overwrite."""

    def __init__(self, clock: Clock) -> None:
        """Bind restore verification to the service clock."""
        self._clock = clock

    def restore(self, request: RecoveryRequest) -> RecoveryReport:
        """Verify, re-encrypt, verify again, then atomically publish a restored database."""
        manifest = request.manifest
        if (
            manifest.status is not BackupStatus.ACTIVE
            or manifest.wrapped_data_key is None
            or manifest.nonce is None
        ):
            raise BackupInvalidatedError(backup_id=manifest.backup_id)
        if request.target_path.exists():
            raise RecoveryTargetError(request.target_path)
        request.target_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = request.target_path.with_name(
            f".{request.target_path.name}.{uuid4()}.recovering"
        )
        try:
            data_key = AESGCM(request.source_keys.backup_wrapping_key()).decrypt(
                manifest.nonce,
                manifest.wrapped_data_key,
                str(manifest.backup_id).encode(),
            )
            backup_keys = _BackupKeyProvider(data_key=data_key, source=request.source_keys)
            with EncryptedStateStore.open(manifest.path, backup_keys, self._clock) as backup:
                _ = backup.verify_audit_chain()
                backup.write_encrypted_backup(
                    temporary,
                    request.replacement_keys.database_key(),
                )
            with EncryptedStateStore.open(
                temporary,
                request.replacement_keys,
                self._clock,
            ) as restored:
                verification = restored.verify_audit_chain()
            _ = temporary.replace(request.target_path)
        except (InvalidTag, StoreKeyError) as error:
            raise BackupInvalidatedError(backup_id=manifest.backup_id) from error
        finally:
            temporary.unlink(missing_ok=True)
        return RecoveryReport(
            path=request.target_path,
            backup_id=manifest.backup_id,
            audit_entries=verification.entries_verified,
            final_audit_hash=verification.final_hash,
            database_key_rotated=True,
        )
