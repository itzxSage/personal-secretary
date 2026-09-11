"""Versioned Life Engine canonical-record migration contract.

A signed ``MigrationManifest`` declares how the existing encrypted foundation
maps into canonical identity/life/conversation records. The migration preserves
record identifiers and the HMAC audit chain, rejects ambiguous identity merges,
and never mints canonical identifiers from OpenClaw external identifiers.
"""

import hmac
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Final, Protocol, Self, final, override
from uuid import UUID

from pydantic import Field, model_validator
from sqlcipher3 import dbapi2 as sqlcipher

from secretary_service.audit import AuditMutation
from secretary_service.canonical import (
    CanonicalIdentity,
    CanonicalRecord,
    CanonicalRecordKind,
    ChannelBinding,
    ConversationEvent,
    ConversationRecord,
    LifeRecord,
    LifeRecordKind,
)
from secretary_service.domain_repository import TABLES
from secretary_service.keys import KeyProvider
from secretary_service.ledger import AuditLedger
from secretary_service.models import (
    EnergyCheckIn,
    Event,
    FrozenModel,
    Goal,
    Identity,
    NonEmpty,
    NormalizedFact,
    PatternSnapshot,
    RecordId,
    RecordKind,
    SourceItem,
    Task,
    TransitionContext,
)

CANONICAL_MIGRATION: Final = Path(__file__).parent / "migrations" / "002_canonical_life_engine.sql"

SUPPORTED_MAPPINGS: Final[Mapping[RecordKind, CanonicalRecordKind]] = {
    RecordKind.IDENTITY: CanonicalRecordKind.IDENTITY,
    RecordKind.GOAL: CanonicalRecordKind.LIFE,
    RecordKind.TASK: CanonicalRecordKind.LIFE,
    RecordKind.EVENT: CanonicalRecordKind.LIFE,
    RecordKind.ENERGY_CHECK_IN: CanonicalRecordKind.LIFE,
    RecordKind.PATTERN_SNAPSHOT: CanonicalRecordKind.LIFE,
    RecordKind.NORMALIZED_FACT: CanonicalRecordKind.LIFE,
    RecordKind.SOURCE_ITEM: CanonicalRecordKind.CONVERSATION,
}

LIFE_MODELS: Final[Mapping[RecordKind, type[FrozenModel]]] = {
    RecordKind.GOAL: Goal,
    RecordKind.TASK: Task,
    RecordKind.EVENT: Event,
    RecordKind.ENERGY_CHECK_IN: EnergyCheckIn,
    RecordKind.PATTERN_SNAPSHOT: PatternSnapshot,
    RecordKind.NORMALIZED_FACT: NormalizedFact,
}

MIN_IDENTITY_MERGE_SOURCES: Final = 2

CANONICAL_TABLES: Final = {
    CanonicalIdentity: "canonical_identities",
    LifeRecord: "canonical_life_records",
    ConversationRecord: "canonical_conversations",
    ConversationEvent: "canonical_conversation_events",
    ChannelBinding: "channel_bindings",
}


class Clock(Protocol):
    """Injectable UTC clock for migration timestamps."""

    def now(self) -> datetime:
        """Return the current aware timestamp."""
        ...


class RecordMapping(FrozenModel):
    """One source foundation kind mapped to one canonical record family."""

    source_kind: RecordKind
    target_kind: CanonicalRecordKind


DEFAULT_RECORD_MAPPINGS: Final = tuple(
    RecordMapping(source_kind=source_kind, target_kind=target_kind)
    for source_kind, target_kind in SUPPORTED_MAPPINGS.items()
)


class IdentityMerge(FrozenModel):
    """Declared merge of two or more source identities into one canonical identity."""

    source_ids: tuple[RecordId, ...]
    target_id: RecordId
    display_name: NonEmpty

    @model_validator(mode="after")
    def _require_multiple_sources(self) -> Self:
        if len(self.source_ids) < MIN_IDENTITY_MERGE_SOURCES:
            message = "identity merge requires at least two source identities"
            raise ValueError(message)
        return self


class MigrationManifest(FrozenModel):
    """Signed contract describing one canonical migration from the foundation."""

    manifest_id: RecordId
    source_schema_version: int = Field(ge=1)
    target_schema_version: int = Field(ge=2)
    record_mappings: tuple[RecordMapping, ...]
    identity_merges: tuple[IdentityMerge, ...] = ()
    conversation_id: RecordId | None = None
    created_at: datetime
    manifest_hash: str = ""


class MigrationReport(FrozenModel):
    """Evidence summary returned after a successful canonical migration."""

    manifest_id: RecordId
    source_schema_version: int
    target_schema_version: int
    copied_records: int
    merged_identities: int
    audit_entries_appended: int
    final_audit_hash: str


@final
class ManifestTamperError(Exception):
    """Migration manifest failed HMAC integrity verification."""

    def __init__(self, manifest_id: str) -> None:
        super().__init__(manifest_id)
        self.manifest_id = manifest_id

    @override
    def __str__(self) -> str:
        return f"migration manifest failed integrity verification: {self.manifest_id}"


@final
class AmbiguousIdentityMergeError(Exception):
    """Declared identity merge has conflicting source identity facts."""

    def __init__(self, target_id: RecordId) -> None:
        super().__init__(target_id)
        self.target_id = target_id

    @override
    def __str__(self) -> str:
        return f"identity merge to {self.target_id} is ambiguous"


@final
class MigrationStateError(Exception):
    """Manifest or database state prevents a safe migration."""


@final
class NonCanonicalIdError(Exception):
    """An external identifier would become a canonical record identifier."""

    def __init__(self, external_id: str) -> None:
        super().__init__(external_id)
        self.external_id = external_id

    @override
    def __str__(self) -> str:
        return f"external identifier would become canonical: {self.external_id}"


def sign_manifest(manifest: MigrationManifest, audit_key: bytes) -> str:
    """Return the HMAC-SHA256 signature over the canonical manifest payload."""
    unsigned = manifest.model_copy(update={"manifest_hash": ""})
    return hmac.digest(audit_key, unsigned.model_dump_json().encode(), "sha256").hex()


def verify_manifest(manifest: MigrationManifest, audit_key: bytes) -> None:
    """Raise ManifestTamperError when the manifest hash is missing or stale."""
    if not manifest.manifest_hash:
        raise ManifestTamperError(str(manifest.manifest_id))
    expected = sign_manifest(manifest, audit_key)
    if not hmac.compare_digest(manifest.manifest_hash, expected):
        raise ManifestTamperError(str(manifest.manifest_id))


def read_applied_manifests(connection: sqlcipher.Connection) -> tuple[MigrationManifest, ...]:
    """Return migration manifests already applied to this state."""
    table_row = connection.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='migration_manifests'"
    ).fetchone()
    if table_row is None or int(table_row[0]) == 0:
        return ()
    rows = connection.execute(
        "SELECT manifest_json FROM migration_manifests ORDER BY applied_at"
    ).fetchall()
    return tuple(MigrationManifest.model_validate_json(str(row[0])) for row in rows)


@final
class MigrationEngine:
    """Apply a signed canonical migration manifest atomically."""

    def __init__(self, connection: sqlcipher.Connection, keys: KeyProvider, clock: Clock) -> None:
        self._connection = connection
        self._keys = keys
        self._clock = clock
        self._ledger = AuditLedger(connection, keys)
        self._appended_audit_entries = 0

    def migrate(self, manifest: MigrationManifest, context: TransitionContext) -> MigrationReport:
        """Verify, copy, merge, and append audit entries in one transaction."""
        _ = self._ledger.verify()
        verify_manifest(manifest, self._keys.audit_key())
        self._assert_schema_version(manifest)
        self._assert_not_applied(manifest)
        self._assert_mapping_supported(manifest)
        self._appended_audit_entries = 0
        _ = self._connection.executescript(CANONICAL_MIGRATION.read_text(encoding="utf-8"))
        self._connection.commit()
        _ = self._connection.execute("BEGIN IMMEDIATE")
        try:
            copied, external_ids = self._copy_records(manifest, context)
            merged = self._apply_identity_merges(manifest, context, external_ids)
            self._append_migration_audit("migration.applied", manifest, context)
            self._record_manifest(manifest)
            _ = self._connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(?, ?)",
                (manifest.target_schema_version, self._clock.now().isoformat()),
            )
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
        verification = self._ledger.verify()
        return MigrationReport(
            manifest_id=manifest.manifest_id,
            source_schema_version=manifest.source_schema_version,
            target_schema_version=manifest.target_schema_version,
            copied_records=copied,
            merged_identities=merged,
            audit_entries_appended=self._appended_audit_entries,
            final_audit_hash=verification.final_hash,
        )

    def _assert_schema_version(self, manifest: MigrationManifest) -> None:
        row = self._connection.execute(
            "SELECT COALESCE(max(version), 0) FROM schema_migrations"
        ).fetchone()
        current = 0 if row is None else int(row[0])
        if manifest.source_schema_version != current:
            message = (
                f"manifest source schema {manifest.source_schema_version} "
                f"does not match database schema {current}"
            )
            raise MigrationStateError(message)
        if manifest.target_schema_version <= current:
            message = (
                f"manifest target schema {manifest.target_schema_version} "
                f"is not beyond database schema {current}"
            )
            raise MigrationStateError(message)

    def _assert_not_applied(self, manifest: MigrationManifest) -> None:
        table_row = self._connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='migration_manifests'"
        ).fetchone()
        if table_row is None or int(table_row[0]) == 0:
            return
        found = self._connection.execute(
            "SELECT manifest_id FROM migration_manifests WHERE manifest_id=?",
            (str(manifest.manifest_id),),
        ).fetchone()
        if found is not None:
            message = f"manifest {manifest.manifest_id} was already applied"
            raise MigrationStateError(message)

    def _assert_mapping_supported(self, manifest: MigrationManifest) -> None:
        for mapping in manifest.record_mappings:
            expected = SUPPORTED_MAPPINGS.get(mapping.source_kind)
            if expected is None or expected != mapping.target_kind:
                message = (
                    f"unsupported record mapping {mapping.source_kind.value} "
                    f"-> {mapping.target_kind.value}"
                )
                raise MigrationStateError(message)
        if (
            any(
                mapping.source_kind is RecordKind.SOURCE_ITEM
                for mapping in manifest.record_mappings
            )
            and manifest.conversation_id is None
        ):
            message = "source_item mapping requires a conversation_id"
            raise MigrationStateError(message)

    def _copy_records(
        self, manifest: MigrationManifest, context: TransitionContext
    ) -> tuple[int, set[str]]:
        copied = 0
        copied_by_kind: dict[CanonicalRecordKind, int] = {}
        external_ids: set[str] = set()
        written_ids: set[str] = set()
        merge_source_ids = {
            str(record_id) for merge in manifest.identity_merges for record_id in merge.source_ids
        }
        for mapping in manifest.record_mappings:
            source_table = TABLES[mapping.source_kind]
            rows = self._connection.execute(
                f"SELECT record_id, state, content_json, created_at FROM {source_table} current WHERE version=(SELECT max(version) FROM {source_table} versions WHERE versions.record_id=current.record_id)",  # noqa: E501, S608
            ).fetchall()
            if mapping.source_kind is RecordKind.SOURCE_ITEM:
                conversation_id = manifest.conversation_id
                if conversation_id is None:
                    message = "source_item mapping requires a conversation_id"
                    raise MigrationStateError(message)
                source_copied = self._copy_source_items(
                    rows, conversation_id, written_ids, external_ids
                )
                copied += source_copied
                copied_by_kind[CanonicalRecordKind.CONVERSATION] = (
                    copied_by_kind.get(CanonicalRecordKind.CONVERSATION, 0) + source_copied
                )
                continue
            for row in rows:
                record_id = RecordId(UUID(str(row[0])))
                if (
                    mapping.source_kind is RecordKind.IDENTITY
                    and str(record_id) in merge_source_ids
                ):
                    continue
                canonical = self._to_canonical(
                    mapping,
                    record_id,
                    str(row[1]),
                    str(row[2]),
                    datetime.fromisoformat(str(row[3])),
                )
                self._insert_canonical(canonical)
                written_ids.add(str(record_id))
                copied += 1
                copied_by_kind[mapping.target_kind] = copied_by_kind.get(mapping.target_kind, 0) + 1
        self._ensure_conversation(
            manifest, written_ids, copied_by_kind.get(CanonicalRecordKind.CONVERSATION, 0)
        )
        self._assert_no_external_id_collision(external_ids, written_ids)
        for kind, count in copied_by_kind.items():
            if count > 0:
                self._append_migration_audit(f"migration.copied.{kind.value}", manifest, context)
        return copied, external_ids

    def _ensure_conversation(
        self,
        manifest: MigrationManifest,
        written_ids: set[str],
        copied_conversation_events: int,
    ) -> None:
        if copied_conversation_events == 0:
            return
        conversation_id = manifest.conversation_id
        if conversation_id is None:
            message = "source_item mapping requires a conversation_id"
            raise MigrationStateError(message)
        if str(conversation_id) in written_ids:
            message = f"conversation id {conversation_id} collides with a copied record"
            raise MigrationStateError(message)
        conversation = ConversationRecord(
            record_id=conversation_id,
            created_at=manifest.created_at,
            state="active",
            title="Migrated inbox",
        )
        self._insert_canonical(conversation)
        written_ids.add(str(conversation_id))

    def _copy_source_items(
        self,
        rows: Sequence[tuple[object, ...]],
        conversation_id: RecordId,
        written_ids: set[str],
        external_ids: set[str],
    ) -> int:
        ordered = sorted(rows, key=lambda row: (datetime.fromisoformat(str(row[3])), str(row[0])))
        for sequence, row in enumerate(ordered, start=1):
            record_id = RecordId(UUID(str(row[0])))
            source = SourceItem.model_validate_json(str(row[2]))
            canonical = ConversationEvent(
                record_id=record_id,
                created_at=datetime.fromisoformat(str(row[3])),
                state=str(row[1]),
                conversation_id=conversation_id,
                sequence=sequence,
                event_type="source_item",
                payload=str(row[2]),
                external_id=source.external_id,
            )
            self._insert_canonical(canonical)
            written_ids.add(str(record_id))
            external_ids.add(source.external_id)
        return len(ordered)

    def _to_canonical(
        self,
        mapping: RecordMapping,
        record_id: RecordId,
        state: str,
        content_json: str,
        created_at: datetime,
    ) -> CanonicalRecord:
        source_kind = mapping.source_kind
        if source_kind is RecordKind.IDENTITY:
            source = Identity.model_validate_json(content_json)
            return CanonicalIdentity(
                record_id=record_id,
                created_at=created_at,
                state=state,
                display_name=source.display_name,
                is_primary=True,
            )
        if source_kind in LIFE_MODELS:
            source = LIFE_MODELS[source_kind].model_validate_json(content_json)
            title = source.title if isinstance(source, Goal | Task | Event) else None
            return LifeRecord(
                record_id=record_id,
                created_at=created_at,
                state=state,
                life_kind=LifeRecordKind(source_kind.value),
                title=title,
                payload=content_json,
            )
        message = f"unsupported mapping {source_kind.value}"
        raise MigrationStateError(message)

    def _apply_identity_merges(
        self,
        manifest: MigrationManifest,
        context: TransitionContext,
        external_ids: set[str],
    ) -> int:
        merged = 0
        for merge in manifest.identity_merges:
            sources: list[Identity] = []
            for source_id in merge.source_ids:
                row = self._connection.execute(
                    "SELECT record_id, state, content_json, created_at FROM identities current WHERE record_id=? AND version=(SELECT max(version) FROM identities versions WHERE versions.record_id=current.record_id)",  # noqa: E501
                    (str(source_id),),
                ).fetchone()
                if row is None:
                    message = f"merge source identity {source_id} was not found"
                    raise MigrationStateError(message)
                sources.append(Identity.model_validate_json(str(row[2])))
            names = {source.display_name for source in sources}
            states = {source.state for source in sources}
            if len(names) != 1 or len(states) != 1 or names != {merge.display_name}:
                raise AmbiguousIdentityMergeError(merge.target_id)
            if str(merge.target_id) in external_ids:
                raise NonCanonicalIdError(str(merge.target_id))
            canonical = CanonicalIdentity(
                record_id=merge.target_id,
                created_at=min(source.created_at for source in sources),
                state=next(iter(states)),
                display_name=merge.display_name,
                is_primary=True,
            )
            self._insert_canonical(canonical)
            self._append_migration_audit(
                "migration.identity_merged", manifest, context, record_id=merge.target_id
            )
            merged += 1
        return merged

    def _insert_canonical(self, canonical: CanonicalRecord) -> None:
        table = CANONICAL_TABLES[type(canonical)]
        _ = self._connection.execute(
            f"INSERT INTO {table} VALUES(?, 1, ?, ?, ?)",
            (
                str(canonical.record_id),
                canonical.state,
                canonical.model_dump_json(),
                canonical.created_at.isoformat(),
            ),
        )

    def _assert_no_external_id_collision(
        self, external_ids: set[str], written_ids: set[str]
    ) -> None:
        collision = external_ids & written_ids
        if collision:
            raise NonCanonicalIdError(next(iter(collision)))

    def _append_migration_audit(
        self,
        action_class: str,
        manifest: MigrationManifest,
        context: TransitionContext,
        *,
        record_id: RecordId | None = None,
    ) -> None:
        fingerprint = hmac.digest(
            self._keys.audit_key(), manifest.model_dump_json().encode(), "sha256"
        ).hex()
        self._ledger.append(
            AuditMutation(
                record_kind=RecordKind.IDENTITY,
                record_id=record_id or manifest.manifest_id,
                state="applied",
                action_class=action_class,
                source_fingerprint=fingerprint,
                context=context,
            )
        )
        self._appended_audit_entries += 1

    def _record_manifest(self, manifest: MigrationManifest) -> None:
        _ = self._connection.execute(
            "INSERT INTO migration_manifests VALUES(?, ?, ?, ?, ?, ?)",
            (
                str(manifest.manifest_id),
                manifest.source_schema_version,
                manifest.target_schema_version,
                manifest.model_dump_json(),
                manifest.manifest_hash,
                self._clock.now().isoformat(),
            ),
        )
