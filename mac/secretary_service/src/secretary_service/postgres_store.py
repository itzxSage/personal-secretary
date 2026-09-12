"""Dedicated-cell PostgreSQL domain unit of work; not wired to production ingress."""

import hmac
import json
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import LiteralString, Self, cast, final
from uuid import UUID

import psycopg
from psycopg import sql

from secretary_service.audit import AuditEntry, AuditTamperError, AuditVerification, sign_entry
from secretary_service.cloud_crypto import CloudCipher, CloudStateKeys
from secretary_service.domain_repository import RecordNotFoundError
from secretary_service.models import (
    DOMAIN_RECORD_ADAPTER,
    DomainRecord,
    RecordId,
    RecordKind,
    Tombstone,
    TransitionContext,
    record_kind,
)
from secretary_service.persistence import DomainRecords
from secretary_service.postgres_devices import PostgresDevicePersistence
from secretary_service.postgres_outbox import PostgresConsumptionStore, PostgresOutbox
from secretary_service.postgres_types import PgConnection


class CellMismatchError(ValueError):
    """Database belongs to a different tenant or unsupported schema."""


@dataclass(frozen=True)
class PostgresExecutionUnit:
    """Domain state, replay markers and outbox share one commit."""

    records: DomainRecords
    consumption: PostgresConsumptionStore
    outbox: PostgresOutbox
    devices: PostgresDevicePersistence


@final
class PostgresStateStore:
    """Serialize domain writers on the dedicated cell's audit-head row.

    One connection belongs to one unit-of-work caller; concurrent callers use
    separate connections. Read committed plus the cell lock orders all mutations.
    No provider requests may execute while this transaction is held.
    """

    def __init__(self, connection: PgConnection, tenant_id: UUID, keys: CloudStateKeys) -> None:
        """Bind a connection to a configured tenant, never request routing input."""
        self._connection = connection
        self._cipher = CloudCipher(tenant_id, keys)
        self._active = False

    @classmethod
    def connect(cls, dsn: str, tenant_id: UUID, keys: CloudStateKeys) -> Self:
        """Open existing state with TLS verification for TCP; never create schema."""
        connection: PgConnection = psycopg.connect(
            dsn, autocommit=True, sslmode="verify-full", connect_timeout=5
        )
        store = cls(connection, tenant_id, keys)
        try:
            with store.domain_transaction():
                pass
        except BaseException:
            connection.close()
            raise
        return store

    @classmethod
    def provision(cls, dsn: str, tenant_id: UUID, keys: CloudStateKeys) -> None:
        """Create an empty synthetic cell; deployment must use a migration identity."""
        with psycopg.connect(dsn, autocommit=True, sslmode="verify-full") as connection:  # noqa: SIM117 -- keep provisioning transaction explicit
            with connection.transaction():
                # Packaged migration SQL, never request-supplied SQL.
                migration = cast(
                    "LiteralString", Path(__file__).with_name("postgres_schema.sql").read_text()
                )
                _ = connection.execute(sql.SQL(migration))
                _ = connection.execute(
                    """INSERT INTO lifeos_cell VALUES (1, %s, 1, 0, %s)
                    ON CONFLICT (singleton) DO NOTHING""",
                    (tenant_id, "0" * 64),
                )
                found = connection.execute(
                    "SELECT tenant_id::text FROM lifeos_cell WHERE singleton=1 FOR UPDATE"
                ).fetchone()
                if found is None or found[0] != str(tenant_id):
                    raise CellMismatchError
        # Validate keys/chain for an existing cell without changing content.
        with cls.connect(dsn, tenant_id, keys):
            pass

    def __enter__(self) -> Self:
        """Return the owned state connection."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the connection, rolling back any unfinished transaction."""
        self._connection.close()

    @contextmanager
    def domain_transaction(self) -> Generator[DomainRecords]:
        """Acquire tenant lock before verifying audit and reading record versions."""
        if self._active:
            message = "use the repository from the active unit of work"
            raise RuntimeError(message)
        with self._connection.transaction():
            row = self._connection.execute(
                """SELECT tenant_id::text, schema_version::text FROM lifeos_cell
                WHERE singleton=1 FOR UPDATE"""
            ).fetchone()
            if row is None or row != (str(self._cipher.tenant_id), "1"):
                raise CellMismatchError
            _ = self._verify_audit_chain()
            records = PostgresDomainRecords(self._connection, self._cipher)
            self._active = True
            try:
                yield records
            finally:
                records.close()
                self._active = False

    def audit_entries(self) -> tuple[AuditEntry, ...]:
        """Read the content-minimized immutable audit chain."""
        rows = self._connection.execute(
            "SELECT entry_json FROM lifeos_audit ORDER BY sequence"
        ).fetchall()
        return tuple(AuditEntry.model_validate_json(row[0]) for row in rows)

    @contextmanager
    def execution_transaction(self) -> Generator[PostgresExecutionUnit]:
        """Compose domain mutation, approval consumption, audit and job enqueue."""
        with self.domain_transaction() as records:
            # Bound to this repository's lifetime, even when a later unit opens.
            def require_active() -> None:
                if not isinstance(records, PostgresDomainRecords):
                    raise TypeError
                records.require_active()

            yield PostgresExecutionUnit(
                records,
                PostgresConsumptionStore(self._connection, require_active),
                PostgresOutbox(self._connection, self._cipher, records, require_active),
                PostgresDevicePersistence(self._connection, self._cipher, records, require_active),
            )

    def verify_audit_chain(self) -> AuditVerification:
        """Verify signatures, order, and the durable head, including lost tail entries."""
        if self._active:
            return self._verify_audit_chain()
        with self.domain_transaction():
            return self._verify_audit_chain()

    def _verify_audit_chain(self) -> AuditVerification:
        # Caller holds the cell lock when this check precedes a mutation.
        previous = "0" * 64
        entries = self.audit_entries()
        for sequence, entry in enumerate(entries, 1):
            if (
                entry.sequence != sequence
                or entry.previous_hash != previous
                or not hmac.compare_digest(
                    entry.entry_hash, sign_entry(entry, self._cipher.keys.audit.get_secret_value())
                )
            ):
                raise AuditTamperError(sequence)
            previous = entry.entry_hash
        head = self._connection.execute(
            "SELECT audit_sequence::text, audit_hash FROM lifeos_cell WHERE singleton=1"
        ).fetchone()
        if head != (str(len(entries)), previous):
            raise AuditTamperError(len(entries) + 1)
        return AuditVerification(entries_verified=len(entries), final_hash=previous)


@final
class PostgresDomainRecords:
    """Transaction-bound encrypted domain repository with no independent commits."""

    def __init__(self, connection: PgConnection, cipher: CloudCipher) -> None:
        """Only the owning store creates repositories after taking the cell lock."""
        self._connection = connection
        self._cipher = cipher
        self._active = True

    def close(self) -> None:
        """Invalidate a repository when its unit of work exits."""
        self._active = False

    def require_active(self) -> None:
        """Reject storage handles that escaped their transaction."""
        if not self._active:
            message = "repository used outside its unit of work"
            raise RuntimeError(message)

    def read(self, kind: RecordKind, record_id: RecordId) -> DomainRecord | None:
        """Decrypt the latest version using its location as authenticated context."""
        self.require_active()
        row = self._connection.execute(
            """SELECT version::text, envelope FROM lifeos_domain WHERE kind=%s AND record_id=%s
            ORDER BY version DESC LIMIT 1""",
            (kind.value, record_id),
        ).fetchone()
        if row is None:
            return None
        return DOMAIN_RECORD_ADAPTER.validate_json(
            self._cipher.open(row[1], (kind.value, str(record_id), row[0]))
        )

    def create(self, record: DomainRecord, context: TransitionContext) -> None:
        """Insert version one with audit; a failed insert rolls back its savepoint."""
        self.require_active()
        with self._connection.transaction():
            deleted = self._connection.execute(
                "SELECT record_id::text FROM lifeos_tombstones WHERE kind=%s AND record_id=%s",
                (record_kind(record).value, record.record_id),
            ).fetchone()
            if deleted is not None:
                message = "deleted record identity cannot be reused"
                raise ValueError(message)
            self._write(record, 1, context, "created")

    def transition(
        self, kind: RecordKind, record_id: RecordId, new_state: str, context: TransitionContext
    ) -> None:
        """Append a validated version while holding the cell's writer lock."""
        self.require_active()
        with self._connection.transaction():
            record = self.read(kind, record_id)
            if record is None:
                raise RecordNotFoundError(kind, record_id)
            row = self._connection.execute(
                "SELECT max(version)::text FROM lifeos_domain WHERE kind=%s AND record_id=%s",
                (kind.value, record_id),
            ).fetchone()
            if row is None:
                raise RecordNotFoundError(kind, record_id)
            changed = DOMAIN_RECORD_ADAPTER.validate_json(
                record.model_copy(update={"state": new_state}).model_dump_json()
            )
            self._write(changed, int(row[0]) + 1, context, "transitioned")

    def _write(
        self, record: DomainRecord, version: int, context: TransitionContext, action: str
    ) -> None:
        kind = record_kind(record)
        envelope = self._cipher.seal(
            record.model_dump_json(), (kind.value, str(record.record_id), str(version))
        )
        _ = self._connection.execute(
            "INSERT INTO lifeos_domain VALUES (%s, %s, %s, %s)",
            (kind.value, record.record_id, version, envelope),
        )
        self._audit(record, context, action)

    def _fingerprint(self, record: DomainRecord) -> str:
        return hmac.digest(
            self._cipher.keys.audit.get_secret_value(), record.model_dump_json().encode(), "sha256"
        ).hex()

    def _audit(self, record: DomainRecord, context: TransitionContext, action: str) -> None:
        row = self._connection.execute(
            "SELECT audit_sequence::text, audit_hash FROM lifeos_cell WHERE singleton=1"
        ).fetchone()
        if row is None:
            raise CellMismatchError
        kind = record_kind(record)
        entry = AuditEntry(
            sequence=int(row[0]) + 1,
            previous_hash=row[1],
            entry_hash="",
            occurred_at=context.occurred_at,
            action_class=f"{kind.value}.{action}",
            record_kind=kind,
            record_id=record.record_id,
            actor=context.actor,
            correlation_id=context.correlation_id,
            source_fingerprint=self._fingerprint(record),
            action_metadata=json.dumps(
                {"state": "deleted" if action == "deleted" else record.state},
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
        signed = entry.model_copy(
            update={"entry_hash": sign_entry(entry, self._cipher.keys.audit.get_secret_value())}
        )
        _ = self._connection.execute(
            "INSERT INTO lifeos_audit VALUES (%s, %s)", (signed.sequence, signed.model_dump_json())
        )
        _ = self._connection.execute(
            "UPDATE lifeos_cell SET audit_sequence=%s, audit_hash=%s WHERE singleton=1",
            (signed.sequence, signed.entry_hash),
        )

    def delete(self, kind: RecordKind, record_id: RecordId, context: TransitionContext) -> None:
        """Purge every version, retaining authenticated deletion evidence and audit."""
        self.require_active()
        with self._connection.transaction():
            record = self.read(kind, record_id)
            if record is None:
                raise RecordNotFoundError(kind, record_id)
            tombstone = Tombstone(
                record_id=record_id,
                record_kind=kind,
                deleted_at=context.occurred_at,
                source_fingerprint=self._fingerprint(record),
            )
            _ = self._connection.execute(
                "INSERT INTO lifeos_tombstones VALUES (%s, %s, %s)",
                (
                    kind.value,
                    record_id,
                    self._cipher.seal(
                        tombstone.model_dump_json(), ("tombstone", kind.value, str(record_id))
                    ),
                ),
            )
            _ = self._connection.execute(
                "DELETE FROM lifeos_domain WHERE kind=%s AND record_id=%s", (kind.value, record_id)
            )
            self._audit(record, context, "deleted")
