"""Append-only domain record repository over SQLCipher."""

import hmac
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Final, final, override

from sqlcipher3 import dbapi2 as sqlcipher

from secretary_service.audit import AuditMutation
from secretary_service.keys import KeyProvider
from secretary_service.ledger import AuditLedger
from secretary_service.models import (
    DOMAIN_RECORD_ADAPTER,
    DomainRecord,
    RecordId,
    RecordKind,
    Tombstone,
    TransitionContext,
    record_kind,
)
from secretary_service.transactions import domain_transaction

TABLES: Final[Mapping[RecordKind, str]] = {
    RecordKind.IDENTITY: "identities",
    RecordKind.CONNECTOR: "connectors",
    RecordKind.CAPABILITY: "capabilities",
    RecordKind.SOURCE_ITEM: "source_items",
    RecordKind.NORMALIZED_FACT: "normalized_facts",
    RecordKind.TASK: "tasks",
    RecordKind.EVENT: "events",
    RecordKind.PROPOSAL: "proposals",
    RecordKind.APPROVAL: "approvals",
    RecordKind.EXECUTION: "executions",
    RecordKind.ENERGY_CHECK_IN: "energy_check_ins",
    RecordKind.GOAL: "goals",
    RecordKind.PATTERN_SNAPSHOT: "pattern_snapshots",
    RecordKind.CONSENT_RECORD: "consent_records",
}


@final
class RecordNotFoundError(Exception):
    """Requested live domain record does not exist."""

    def __init__(self, record_kind: RecordKind, record_id: RecordId) -> None:
        super().__init__(record_kind, record_id)
        self.record_kind = record_kind
        self.record_id = record_id

    @override
    def __str__(self) -> str:
        return f"{self.record_kind.value} {self.record_id} was not found"


@final
class DomainRepository:
    """Persist domain versions and their audit facts in one transaction."""

    def __init__(
        self,
        connection: sqlcipher.Connection,
        keys: KeyProvider,
        ledger: AuditLedger,
        authorize_delete: Callable[[], None],
        revoke_delete: Callable[[], None],
    ) -> None:
        self._connection = connection
        self._keys = keys
        self._ledger = ledger
        self._authorize_delete = authorize_delete
        self._revoke_delete = revoke_delete

    def _fingerprint(self, record: DomainRecord) -> str:
        return hmac.digest(
            self._keys.audit_key(), record.model_dump_json().encode(), "sha256"
        ).hex()

    def create(self, record: DomainRecord, context: TransitionContext) -> None:
        """Create version one and its chained audit entry atomically."""
        kind = record_kind(record)
        with domain_transaction(self._connection):
            _ = self._ledger.verify()
            deleted = self._connection.execute(
                "SELECT record_id FROM tombstones WHERE record_id=?", (str(record.record_id),)
            ).fetchone()
            if deleted is not None:
                message = "deleted record identity cannot be reused"
                raise ValueError(message)
            _ = self._connection.execute(
                f"INSERT INTO {TABLES[kind]} VALUES(?, 1, ?, ?, ?)",
                (
                    str(record.record_id),
                    record.state,
                    record.model_dump_json(),
                    record.created_at.isoformat(),
                ),
            )
            self._ledger.append(
                AuditMutation(
                    record_kind=kind,
                    record_id=record.record_id,
                    state=record.state,
                    action_class=f"{kind.value}.created",
                    source_fingerprint=self._fingerprint(record),
                    context=context,
                )
            )

    def transition(
        self,
        kind: RecordKind,
        record_id: RecordId,
        new_state: str,
        context: TransitionContext,
    ) -> None:
        """Append a new immutable state version and audit entry."""
        with domain_transaction(self._connection):
            _ = self._ledger.verify()
            self._transition(kind, record_id, new_state, context)

    def _transition(
        self,
        kind: RecordKind,
        record_id: RecordId,
        new_state: str,
        context: TransitionContext,
    ) -> None:
        current = self.read(kind, record_id)
        if current is None:
            raise RecordNotFoundError(record_kind=kind, record_id=record_id)
        version = self.version_count(kind, record_id) + 1
        changed = DOMAIN_RECORD_ADAPTER.validate_json(
            current.model_copy(update={"state": new_state}).model_dump_json()
        )
        _ = self._connection.execute(
            f"INSERT INTO {TABLES[kind]} VALUES(?, ?, ?, ?, ?)",
            (
                str(record_id),
                version,
                new_state,
                changed.model_dump_json(),
                current.created_at.isoformat(),
            ),
        )
        self._ledger.append(
            AuditMutation(
                record_kind=kind,
                record_id=record_id,
                state=new_state,
                action_class=f"{kind.value}.transitioned",
                source_fingerprint=self._fingerprint(changed),
                context=context,
            )
        )

    def read(self, kind: RecordKind, record_id: RecordId) -> DomainRecord | None:
        """Return the latest live version of one record."""
        row = self._connection.execute(
            f"SELECT content_json FROM {TABLES[kind]} WHERE record_id=? ORDER BY version DESC LIMIT 1",  # noqa: E501, S608
            (str(record_id),),
        ).fetchone()
        return None if row is None else DOMAIN_RECORD_ADAPTER.validate_json(str(row[0]))

    def records(self) -> tuple[DomainRecord, ...]:
        """Return latest versions of all live domain records."""
        found: list[DomainRecord] = []
        for table in TABLES.values():
            rows = self._connection.execute(
                f"SELECT content_json FROM {table} current WHERE version=(SELECT max(version) FROM {table} versions WHERE versions.record_id=current.record_id)",  # noqa: E501, S608
            ).fetchall()
            found.extend(DOMAIN_RECORD_ADAPTER.validate_json(str(row[0])) for row in rows)
        return tuple(found)

    def version_count(self, kind: RecordKind, record_id: RecordId) -> int:
        """Count immutable versions for one record."""
        row = self._connection.execute(
            f"SELECT count(*) FROM {TABLES[kind]} WHERE record_id=?",  # noqa: S608
            (str(record_id),),
        ).fetchone()
        return 0 if row is None else int(row[0])

    def delete(self, kind: RecordKind, record_id: RecordId, context: TransitionContext) -> None:
        """Purge all content versions and retain a keyed tombstone."""
        with domain_transaction(self._connection):
            _ = self._ledger.verify()
            self._delete(kind, record_id, context)

    def _delete(self, kind: RecordKind, record_id: RecordId, context: TransitionContext) -> None:
        current = self.read(kind, record_id)
        if current is None:
            raise RecordNotFoundError(record_kind=kind, record_id=record_id)
        fingerprint = self._fingerprint(current)
        self._authorize_delete()
        try:
            _ = self._connection.execute(
                f"DELETE FROM {TABLES[kind]} WHERE record_id=?",  # noqa: S608
                (str(record_id),),
            )
        finally:
            self._revoke_delete()
        _ = self._connection.execute(
            "INSERT INTO tombstones VALUES(?, ?, ?, ?)",
            (str(record_id), kind.value, context.occurred_at.isoformat(), fingerprint),
        )
        self._ledger.append(
            AuditMutation(
                record_kind=kind,
                record_id=record_id,
                state="deleted",
                action_class=f"{kind.value}.deleted",
                source_fingerprint=fingerprint,
                context=context,
            )
        )

    def tombstone(self, record_id: RecordId) -> Tombstone:
        """Return pseudonymous deletion evidence."""
        row = self._connection.execute(
            "SELECT record_kind, deleted_at, source_fingerprint FROM tombstones WHERE record_id=?",
            (str(record_id),),
        ).fetchone()
        if row is None:
            raise RecordNotFoundError(record_kind=RecordKind.SOURCE_ITEM, record_id=record_id)
        return Tombstone(
            record_id=record_id,
            record_kind=RecordKind(str(row[0])),
            deleted_at=datetime.fromisoformat(str(row[1])),
            source_fingerprint=str(row[2]),
        )
