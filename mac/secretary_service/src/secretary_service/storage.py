"""Key-first SQLCipher repository with append-only domain versions."""

import hmac
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Final, Protocol, Self, final, override

from sqlcipher3 import dbapi2 as sqlcipher

from secretary_service.audit import AuditEntry, AuditMutation, AuditVerification
from secretary_service.authority_consumption import EncryptedConsumptionStore
from secretary_service.backup_catalog import BackupCatalog
from secretary_service.canonical import CanonicalRepository, CanonicalSnapshot
from secretary_service.device_persistence import EncryptedDevicePersistence
from secretary_service.domain_repository import DomainRepository
from secretary_service.goals import GoalGraphRepository
from secretary_service.keys import KeyProvider
from secretary_service.learning import (
    LearningAuditor,
    LearningLifecycle,
    LearningRepository,
)
from secretary_service.ledger import AuditLedger
from secretary_service.memory_repository import MemoryAuditor, MemoryLifecycle, MemoryRepository
from secretary_service.migration import (
    CANONICAL_MIGRATION,
    MigrationEngine,
    MigrationManifest,
    MigrationReport,
    read_applied_manifests,
)
from secretary_service.models import (
    BackupId,
    BackupManifest,
    BackupStatus,
    DomainRecord,
    RecordId,
    RecordKind,
    Tombstone,
    TransitionContext,
)
from secretary_service.persistence import DomainRecords
from secretary_service.relay_store import CONVERSATION_RETENTION, ConversationRelayStore
from secretary_service.transactions import domain_transaction

MIGRATION: Final = Path(__file__).parent / "migrations" / "001_encrypted_domain_state.sql"
GOALS_MEMORY_MIGRATION: Final = Path(__file__).parent / "migrations" / "003_goals_memory.sql"
LEARNING_MIGRATION: Final = Path(__file__).parent / "migrations" / "004_learning.sql"
AUTHORITY_MIGRATION: Final = Path(__file__).parent / "migrations" / "005_authority_consumption.sql"
DEVICE_MIGRATION: Final = Path(__file__).parent / "migrations" / "006_device_enrollment.sql"
RELAY_MIGRATION: Final = Path(__file__).parent / "migrations" / "007_conversation_relay.sql"
RETENTION_MIGRATION: Final = Path(__file__).parent / "migrations" / "008_conversation_retention.sql"
LIFE_MODEL_MIGRATION: Final = (
    Path(__file__).parent / "migrations" / "009_life_model_backup_coverage.sql"
)


def _require_cipher(row: tuple[str | bytes, ...] | None, path: Path) -> None:
    if row is None or not str(row[0]):
        raise StoreKeyError(path=path)


class Clock(Protocol):
    """Injectable UTC clock for persistence and lifecycle jobs."""

    def now(self) -> datetime:
        """Return the current aware timestamp."""
        ...


@final
class StoreKeyError(Exception):
    """SQLCipher state failed authentication with the provided key."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.path = path

    @override
    def __str__(self) -> str:
        return f"encrypted state could not be opened: {self.path}"


@final
class EncryptedStateStore:
    """Own encrypted persistence and verified state transitions."""

    def __init__(
        self,
        path: Path,
        connection: sqlcipher.Connection,
        keys: KeyProvider,
        clock: Clock,
    ) -> None:
        self.path = path
        self._connection = connection
        self._keys = keys
        self._clock = clock
        self._lifecycle_delete_allowed = False
        self.authority_consumption = EncryptedConsumptionStore(connection)
        self.devices = EncryptedDevicePersistence(connection)
        self._ledger = AuditLedger(connection, keys)
        self.conversations = ConversationRelayStore(
            connection,
            keys,
            self._ledger,
            self._authorize_lifecycle_delete,
            self._revoke_lifecycle_delete,
        )
        self._backup_catalog = BackupCatalog(connection)
        self._canonical = CanonicalRepository(connection)
        self._domain = DomainRepository(
            connection,
            keys,
            self._ledger,
            self._authorize_lifecycle_delete,
            self._revoke_lifecycle_delete,
        )
        self.goals = GoalGraphRepository(connection, keys, self._ledger)
        self.memory = MemoryRepository(
            connection,
            MemoryAuditor(keys, self._ledger),
            MemoryLifecycle(
                clock,
                self._authorize_lifecycle_delete,
                self._revoke_lifecycle_delete,
            ),
        )
        self.learning = LearningRepository(
            connection,
            LearningAuditor(keys, self._ledger),
            LearningLifecycle(
                clock,
                self._authorize_lifecycle_delete,
                self._revoke_lifecycle_delete,
            ),
        )

    @classmethod
    def open(cls, path: Path, keys: KeyProvider, clock: Clock) -> Self:
        """Open key-first, migrate schema, and verify audit integrity."""
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlcipher.connect(str(path))
        try:
            key_hex = keys.database_key().hex()
            _ = connection.execute(f"PRAGMA key = \"x'{key_hex}'\"")
            cipher_row = connection.execute("PRAGMA cipher_version").fetchone()
            _require_cipher(cipher_row, path)
            _ = connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
            _ = connection.execute("PRAGMA cipher_memory_security = ON")
            _ = connection.execute("PRAGMA foreign_keys = ON")
        except (sqlcipher.DatabaseError, StoreKeyError) as error:
            connection.close()
            raise StoreKeyError(path=path) from error
        store = cls(path, connection, keys, clock)
        connection.create_function("lifecycle_authorized", 0, store._lifecycle_authorized)
        try:
            store._migrate()
            _ = store.verify_audit_chain()
        except BaseException:
            connection.close()
            raise
        return store

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._connection.close()

    def _migrate(self) -> None:
        _ = self._connection.executescript(MIGRATION.read_text(encoding="utf-8"))
        _ = self._connection.executescript(GOALS_MEMORY_MIGRATION.read_text(encoding="utf-8"))
        _ = self._connection.executescript(LEARNING_MIGRATION.read_text(encoding="utf-8"))
        _ = self._connection.executescript(AUTHORITY_MIGRATION.read_text(encoding="utf-8"))
        _ = self._connection.executescript(DEVICE_MIGRATION.read_text(encoding="utf-8"))
        _ = self._connection.executescript(CANONICAL_MIGRATION.read_text(encoding="utf-8"))
        _ = self._connection.executescript(RELAY_MIGRATION.read_text(encoding="utf-8"))
        _ = self._connection.executescript(RETENTION_MIGRATION.read_text(encoding="utf-8"))
        _ = self._connection.executescript(LIFE_MODEL_MIGRATION.read_text(encoding="utf-8"))
        missing_retention = self._connection.execute(
            """SELECT r.conversation_id, c.created_at FROM relay_conversations r
            JOIN canonical_conversations c ON c.record_id=r.conversation_id
            LEFT JOIN relay_retention x ON x.conversation_id=r.conversation_id
            WHERE x.conversation_id IS NULL
            AND c.version=(SELECT max(v.version) FROM canonical_conversations v
                           WHERE v.record_id=c.record_id)"""
        ).fetchall()
        _ = self._connection.executemany(
            "INSERT INTO relay_retention VALUES (?, ?)",
            (
                (
                    str(row[0]),
                    (datetime.fromisoformat(str(row[1])) + CONVERSATION_RETENTION).isoformat(),
                )
                for row in missing_retention
            ),
        )
        _ = self._connection.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(1, ?)",
            (self._clock.now().isoformat(),),
        )
        self._connection.commit()

    def _lifecycle_authorized(self) -> int:
        return int(self._lifecycle_delete_allowed)

    def _authorize_lifecycle_delete(self) -> None:
        self._lifecycle_delete_allowed = True

    def _revoke_lifecycle_delete(self) -> None:
        self._lifecycle_delete_allowed = False

    def create(self, record: DomainRecord, context: TransitionContext) -> None:
        """Create version one and its chained audit entry atomically."""
        self._domain.create(record, context)

    @contextmanager
    def domain_transaction(self) -> Generator[DomainRecords]:
        """Commit domain records and their audit together; roll back on failure."""
        with domain_transaction(self._connection):
            _ = self.verify_audit_chain()
            yield self._domain

    def transition(
        self,
        kind: RecordKind,
        record_id: RecordId,
        new_state: str,
        context: TransitionContext,
    ) -> None:
        """Append a new immutable state version and audit entry."""
        self._domain.transition(kind, record_id, new_state, context)

    def read(self, kind: RecordKind, record_id: RecordId) -> DomainRecord | None:
        """Return the latest live version of one record."""
        return self._domain.read(kind, record_id)

    def records(self) -> tuple[DomainRecord, ...]:
        """Return the latest version of every live domain record."""
        return self._domain.records()

    def version_count(self, kind: RecordKind, record_id: RecordId) -> int:
        """Count immutable versions for one record."""
        return self._domain.version_count(kind, record_id)

    def audit_entries(self) -> tuple[AuditEntry, ...]:
        """Return ordered, content-minimized audit entries."""
        return self._ledger.entries()

    def verify_audit_chain(self) -> AuditVerification:
        """Verify sequence, predecessor links, and keyed signatures."""
        return self._ledger.verify()

    def prune_audit_before(self, cutoff: datetime) -> int:
        """Expire only a contiguous verified audit prefix while retaining its chain anchor."""
        return self._ledger.prune_before(
            cutoff,
            self._authorize_lifecycle_delete,
            self._revoke_lifecycle_delete,
        )

    def append_export_audit(self, marker_id: RecordId, context: TransitionContext) -> None:
        """Append proof of a user-requested redacted export."""
        _ = self.verify_audit_chain()
        fingerprint = hmac.digest(self._keys.audit_key(), str(marker_id).encode(), "sha256").hex()
        self._ledger.append(
            AuditMutation(
                record_kind=RecordKind.IDENTITY,
                record_id=marker_id,
                state="redacted",
                action_class="export.redacted",
                source_fingerprint=fingerprint,
                context=context,
            )
        )
        self._connection.commit()

    def delete_record(
        self, kind: RecordKind, record_id: RecordId, context: TransitionContext
    ) -> None:
        """Purge content versions and retain a keyed tombstone."""
        self._domain.delete(kind, record_id, context)

    def tombstone(self, record_id: RecordId) -> Tombstone:
        """Return pseudonymous deletion evidence."""
        return self._domain.tombstone(record_id)

    def write_encrypted_backup(self, path: Path, data_key: bytes) -> None:
        """Copy state to SQLCipher under a unique data key."""
        target = sqlcipher.connect(str(path))
        try:
            _ = target.execute(f"PRAGMA key = \"x'{data_key.hex()}'\"")
            self._connection.backup(target)
            target.commit()
        finally:
            target.close()

    def register_backup(self, manifest: BackupManifest) -> None:
        """Persist a backup envelope and contained-record index."""
        self._backup_catalog.register(manifest, self.records(), memory_ids=self.memory.record_ids())

    def backup_manifest(self, backup_id: BackupId) -> BackupManifest:
        """Read one durable backup manifest."""
        return self._backup_catalog.manifest(backup_id)

    def backup_manifests(self) -> tuple[BackupManifest, ...]:
        """Read all durable backup manifests."""
        return self._backup_catalog.manifests()

    def backup_ids_containing(self, kind: RecordKind, record_id: RecordId) -> tuple[BackupId, ...]:
        """Find backup sets containing one record."""
        return self._backup_catalog.containing(kind, record_id)

    def destroy_backup_key(self, backup_id: BackupId, status: BackupStatus) -> None:
        """Erase a backup envelope to make its ciphertext unrecoverable."""
        self._backup_catalog.destroy_key(backup_id, status)

    def migrate_canonical(
        self, manifest: MigrationManifest, context: TransitionContext
    ) -> MigrationReport:
        """Apply a signed canonical migration manifest atomically."""
        return MigrationEngine(self._connection, self._keys, self._clock).migrate(manifest, context)

    def canonical_records(self) -> CanonicalSnapshot:
        """Return the latest live version of every canonical record family."""
        return self._canonical.snapshot()

    def applied_manifests(self) -> tuple[MigrationManifest, ...]:
        """Return migration manifests already applied to this state."""
        return read_applied_manifests(self._connection)
