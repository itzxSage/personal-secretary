"""Life Engine canonical-record migration contract tests.

Todo 2: versioned canonical identity/life/conversation/audit records and a
migration manifest from the existing encrypted foundation. OpenClaw identifiers
are never canonical; the audit chain survives the migration unbroken.
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from secretary_service.keys import DeterministicTestKeyProvider
from secretary_service.migration import (
    DEFAULT_RECORD_MAPPINGS,
    AmbiguousIdentityMergeError,
    IdentityMerge,
    ManifestTamperError,
    MigrationEngine,
    MigrationManifest,
    MigrationStateError,
    NonCanonicalIdError,
    sign_manifest,
)
from secretary_service.models import (
    ActorId,
    Connector,
    CorrelationId,
    Event,
    Goal,
    Identity,
    RecordId,
    RecordKind,
    SourceItem,
    Task,
    TransitionContext,
)
from secretary_service.storage import EncryptedStateStore
from tests.helpers import FakeClock


def context(clock: FakeClock, actor: str, correlation_id: str) -> TransitionContext:
    return TransitionContext(
        actor=ActorId(actor),
        correlation_id=CorrelationId(correlation_id),
        occurred_at=clock.now(),
    )


def seed_foundation(store: EncryptedStateStore, clock: FakeClock) -> dict[str, RecordId]:
    """Seed a fake old encrypted-state database with identity/life/conversation records."""
    ids: dict[str, RecordId] = {}
    ids["identity"] = RecordId(UUID("10000000-0000-0000-0000-000000000001"))
    store.create(
        Identity(
            record_id=ids["identity"],
            created_at=clock.now(),
            state="active",
            display_name="Jared",
        ),
        context(clock, "foundation", "corr-seed"),
    )
    ids["goal"] = RecordId(UUID("10000000-0000-0000-0000-000000000002"))
    store.create(
        Goal(
            record_id=ids["goal"],
            created_at=clock.now(),
            state="active",
            title="Ship LifeOS",
        ),
        context(clock, "foundation", "corr-seed"),
    )
    ids["task"] = RecordId(UUID("10000000-0000-0000-0000-000000000003"))
    store.create(
        Task(
            record_id=ids["task"],
            created_at=clock.now(),
            state="active",
            title="Define migration contract",
        ),
        context(clock, "foundation", "corr-seed"),
    )
    ids["event"] = RecordId(UUID("10000000-0000-0000-0000-000000000004"))
    store.create(
        Event(
            record_id=ids["event"],
            created_at=clock.now(),
            state="scheduled",
            title="Standup",
            starts_at=datetime(2026, 9, 7, 9, 0, tzinfo=UTC),
        ),
        context(clock, "foundation", "corr-seed"),
    )
    ids["source_item"] = RecordId(UUID("10000000-0000-0000-0000-000000000005"))
    store.create(
        SourceItem(
            record_id=ids["source_item"],
            created_at=clock.now(),
            source_type="communication",
            external_id="oc-msg-42",
            raw_content="private fixture body",
        ),
        context(clock, "foundation", "corr-seed"),
    )
    ids["connector"] = RecordId(UUID("10000000-0000-0000-0000-000000000006"))
    store.create(
        Connector(
            record_id=ids["connector"],
            created_at=clock.now(),
            state="active",
            name="telegram",
            secret_reference="connector-telegram",  # noqa: S106
        ),
        context(clock, "foundation", "corr-seed"),
    )
    return ids


def signed_manifest(  # noqa: PLR0913
    clock: FakeClock,
    keys: DeterministicTestKeyProvider,
    *,
    manifest_id: RecordId | None = None,
    conversation_id: RecordId | None = None,
    identity_merges: tuple[IdentityMerge, ...] = (),
    source_schema_version: int = 1,
    target_schema_version: int = 2,
) -> MigrationManifest:
    """Build a manifest signed with the audit key for the default record mappings."""
    manifest = MigrationManifest(
        manifest_id=manifest_id or RecordId(UUID("20000000-0000-0000-0000-000000000001")),
        source_schema_version=source_schema_version,
        target_schema_version=target_schema_version,
        record_mappings=DEFAULT_RECORD_MAPPINGS,
        identity_merges=identity_merges,
        conversation_id=conversation_id,
        created_at=clock.now(),
    )
    return manifest.model_copy(update={"manifest_hash": sign_manifest(manifest, keys.audit_key())})


def test_baseline_foundation_roundtrip_preserves_ids_and_audit_chain(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    """Characterize the existing encrypted foundation the migration builds on."""
    ids = seed_foundation(store, clock)

    assert store.read(RecordKind.IDENTITY, ids["identity"]) is not None
    assert store.read(RecordKind.GOAL, ids["goal"]) is not None
    assert store.read(RecordKind.TASK, ids["task"]) is not None
    assert store.read(RecordKind.EVENT, ids["event"]) is not None
    assert store.read(RecordKind.SOURCE_ITEM, ids["source_item"]) is not None
    assert store.read(RecordKind.CONNECTOR, ids["connector"]) is not None

    verification = store.verify_audit_chain()
    assert verification.entries_verified == 6
    assert len(store.audit_entries()) == 6
    assert [entry.sequence for entry in store.audit_entries()] == [1, 2, 3, 4, 5, 6]


def test_migration_preserves_record_ids_and_audit_chain(
    store: EncryptedStateStore,
    keys: DeterministicTestKeyProvider,
    clock: FakeClock,
) -> None:
    """A signed manifest migrates the foundation and keeps IDs and the audit chain intact."""
    ids = seed_foundation(store, clock)
    conversation_id = RecordId(UUID("20000000-0000-0000-0000-0000000000aa"))
    manifest = signed_manifest(clock, keys, conversation_id=conversation_id)

    report = store.migrate_canonical(manifest, context(clock, "migration", "corr-migrate"))

    assert report.copied_records == 5
    assert report.merged_identities == 0
    assert report.audit_entries_appended == 4

    snapshot = store.canonical_records()
    assert [record.record_id for record in snapshot.identities] == [ids["identity"]]
    assert [record.record_id for record in snapshot.life_records] == [
        ids["goal"],
        ids["task"],
        ids["event"],
    ]
    assert [record.record_id for record in snapshot.conversation_events] == [ids["source_item"]]
    assert [record.record_id for record in snapshot.conversations] == [conversation_id]

    identity = snapshot.identities[0]
    assert identity.display_name == "Jared"
    assert identity.is_primary is True
    assert identity.state == "active"

    life = snapshot.life_records[0]
    assert life.life_kind.value == "goal"
    assert life.title == "Ship LifeOS"

    event = snapshot.conversation_events[0]
    assert event.conversation_id == conversation_id
    assert event.sequence == 1
    assert event.external_id == "oc-msg-42"

    verification = store.verify_audit_chain()
    assert verification.entries_verified == 6 + report.audit_entries_appended
    assert verification.final_hash == report.final_audit_hash

    assert store.read(RecordKind.IDENTITY, ids["identity"]) is not None
    assert store.read(RecordKind.SOURCE_ITEM, ids["source_item"]) is not None
    entries = store.audit_entries()
    assert [entry.sequence for entry in entries] == list(range(1, 11))
    assert entries[5].entry_hash == entries[6].previous_hash


def test_ambiguous_identity_merge_is_rejected(
    store: EncryptedStateStore,
    keys: DeterministicTestKeyProvider,
    clock: FakeClock,
) -> None:
    """Conflicting source identities make a declared merge ambiguous and fail closed."""
    first = RecordId(UUID("10000000-0000-0000-0000-000000000011"))
    second = RecordId(UUID("10000000-0000-0000-0000-000000000012"))
    store.create(
        Identity(
            record_id=first,
            created_at=clock.now(),
            state="active",
            display_name="Jared",
        ),
        context(clock, "foundation", "corr-merge"),
    )
    store.create(
        Identity(
            record_id=second,
            created_at=clock.now(),
            state="active",
            display_name="Jared Smith",
        ),
        context(clock, "foundation", "corr-merge"),
    )
    target = RecordId(UUID("20000000-0000-0000-0000-0000000000bb"))
    manifest = signed_manifest(
        clock,
        keys,
        conversation_id=RecordId(UUID("20000000-0000-0000-0000-0000000000aa")),
        identity_merges=(
            IdentityMerge(source_ids=(first, second), target_id=target, display_name="Jared"),
        ),
    )

    with pytest.raises(AmbiguousIdentityMergeError):
        _ = store.migrate_canonical(manifest, context(clock, "migration", "corr-migrate"))

    assert store.canonical_records().identities == ()
    assert store.applied_manifests() == ()
    assert store.verify_audit_chain().entries_verified == 2


def test_unambiguous_identity_merge_succeeds(
    store: EncryptedStateStore,
    keys: DeterministicTestKeyProvider,
    clock: FakeClock,
) -> None:
    """Identical source identities merge into one canonical identity."""
    first = RecordId(UUID("10000000-0000-0000-0000-000000000013"))
    second = RecordId(UUID("10000000-0000-0000-0000-000000000014"))
    store.create(
        Identity(
            record_id=first,
            created_at=clock.now(),
            state="active",
            display_name="Jared",
        ),
        context(clock, "foundation", "corr-merge"),
    )
    store.create(
        Identity(
            record_id=second,
            created_at=clock.now(),
            state="active",
            display_name="Jared",
        ),
        context(clock, "foundation", "corr-merge"),
    )
    target = RecordId(UUID("20000000-0000-0000-0000-0000000000cc"))
    manifest = signed_manifest(
        clock,
        keys,
        conversation_id=RecordId(UUID("20000000-0000-0000-0000-0000000000aa")),
        identity_merges=(
            IdentityMerge(source_ids=(first, second), target_id=target, display_name="Jared"),
        ),
    )

    report = store.migrate_canonical(manifest, context(clock, "migration", "corr-migrate"))

    assert report.copied_records == 0
    assert report.merged_identities == 1
    assert report.audit_entries_appended == 2
    snapshot = store.canonical_records()
    assert [record.record_id for record in snapshot.identities] == [target]
    assert snapshot.identities[0].display_name == "Jared"
    assert snapshot.identities[0].is_primary is True


def test_tampered_manifest_fails(
    store: EncryptedStateStore,
    keys: DeterministicTestKeyProvider,
    clock: FakeClock,
) -> None:
    """Any manifest field change or forged hash fails integrity verification."""
    conversation_id = RecordId(UUID("20000000-0000-0000-0000-0000000000aa"))
    manifest = signed_manifest(clock, keys, conversation_id=conversation_id)

    tampered = manifest.model_copy(update={"target_schema_version": 3})
    with pytest.raises(ManifestTamperError):
        _ = store.migrate_canonical(tampered, context(clock, "migration", "corr-tamper"))

    forged = manifest.model_copy(update={"manifest_hash": "0" * 64})
    with pytest.raises(ManifestTamperError):
        _ = store.migrate_canonical(forged, context(clock, "migration", "corr-tamper"))

    assert store.applied_manifests() == ()


def test_openclaw_ids_are_not_canonical(
    store: EncryptedStateStore,
    keys: DeterministicTestKeyProvider,
    clock: FakeClock,
) -> None:
    """OpenClaw identifiers survive only as non-canonical external references."""
    ids = seed_foundation(store, clock)
    conversation_id = RecordId(UUID("20000000-0000-0000-0000-0000000000aa"))
    manifest = signed_manifest(clock, keys, conversation_id=conversation_id)

    _ = store.migrate_canonical(manifest, context(clock, "migration", "corr-migrate"))

    snapshot = store.canonical_records()
    event = snapshot.conversation_events[0]
    assert event.record_id == ids["source_item"]
    assert event.external_id == "oc-msg-42"
    canonical_ids = {
        str(record.record_id)
        for records in (
            snapshot.identities,
            snapshot.life_records,
            snapshot.conversations,
            snapshot.conversation_events,
            snapshot.channel_bindings,
        )
        for record in records
    }
    assert "oc-msg-42" not in canonical_ids


def test_external_id_cannot_become_canonical(
    store: EncryptedStateStore,
    keys: DeterministicTestKeyProvider,
    clock: FakeClock,
) -> None:
    """A manifest that would mint a canonical ID from an external ID is rejected."""
    ids = seed_foundation(store, clock)
    external_uuid = "30000000-0000-0000-0000-000000000099"
    store.create(
        SourceItem(
            record_id=RecordId(UUID("10000000-0000-0000-0000-000000000007")),
            created_at=clock.now(),
            source_type="communication",
            external_id=external_uuid,
            raw_content="oc payload",
        ),
        context(clock, "foundation", "corr-ext"),
    )
    second_identity = RecordId(UUID("10000000-0000-0000-0000-000000000008"))
    store.create(
        Identity(
            record_id=second_identity,
            created_at=clock.now(),
            state="active",
            display_name="Jared",
        ),
        context(clock, "foundation", "corr-ext"),
    )
    conversation_id = RecordId(UUID("20000000-0000-0000-0000-0000000000aa"))
    manifest = signed_manifest(
        clock,
        keys,
        conversation_id=conversation_id,
        identity_merges=(
            IdentityMerge(
                source_ids=(ids["identity"], second_identity),
                target_id=RecordId(UUID(external_uuid)),
                display_name="Jared",
            ),
        ),
    )

    with pytest.raises(NonCanonicalIdError):
        _ = store.migrate_canonical(manifest, context(clock, "migration", "corr-migrate"))

    assert store.canonical_records().identities == ()
    assert store.applied_manifests() == ()


def test_stale_migration_state_is_rejected(
    store: EncryptedStateStore,
    keys: DeterministicTestKeyProvider,
    clock: FakeClock,
) -> None:
    """An already-applied manifest cannot be replayed."""
    conversation_id = RecordId(UUID("20000000-0000-0000-0000-0000000000aa"))
    manifest = signed_manifest(clock, keys, conversation_id=conversation_id)

    _ = store.migrate_canonical(manifest, context(clock, "migration", "corr-migrate"))

    with pytest.raises(MigrationStateError):
        _ = store.migrate_canonical(manifest, context(clock, "migration", "corr-replay"))

    assert len(store.applied_manifests()) == 1


def test_manifest_source_schema_must_match_database(
    store: EncryptedStateStore,
    keys: DeterministicTestKeyProvider,
    clock: FakeClock,
) -> None:
    """A manifest claiming a source schema the database has not reached is stale."""
    conversation_id = RecordId(UUID("20000000-0000-0000-0000-0000000000aa"))
    forward = signed_manifest(
        clock,
        keys,
        manifest_id=RecordId(UUID("20000000-0000-0000-0000-000000000002")),
        source_schema_version=2,
        target_schema_version=3,
        conversation_id=conversation_id,
    )

    with pytest.raises(MigrationStateError):
        _ = store.migrate_canonical(forward, context(clock, "migration", "corr-forward"))

    assert store.applied_manifests() == ()


def test_malformed_manifest_is_rejected(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    """Malformed JSON and unsigned manifests fail before any state change."""
    with pytest.raises(ValidationError):
        _ = MigrationManifest.model_validate_json('{"manifest_id": "not-a-uuid"}')

    unsigned = MigrationManifest(
        manifest_id=RecordId(UUID("20000000-0000-0000-0000-000000000003")),
        source_schema_version=1,
        target_schema_version=2,
        record_mappings=DEFAULT_RECORD_MAPPINGS,
        conversation_id=RecordId(UUID("20000000-0000-0000-0000-0000000000aa")),
        created_at=clock.now(),
    )
    with pytest.raises(ManifestTamperError):
        _ = store.migrate_canonical(unsigned, context(clock, "migration", "corr-unsigned"))

    assert store.applied_manifests() == ()


def test_mid_operation_interruption_leaves_no_partial_state(
    store: EncryptedStateStore,
    keys: DeterministicTestKeyProvider,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An interrupted migration rolls back records, audit entries, and manifest state."""
    ids = seed_foundation(store, clock)
    conversation_id = RecordId(UUID("20000000-0000-0000-0000-0000000000aa"))
    manifest = signed_manifest(clock, keys, conversation_id=conversation_id)
    original = MigrationEngine._copy_records  # pyright: ignore[reportPrivateUsage]
    raised = False

    def interrupted(
        self: MigrationEngine,
        manifest: MigrationManifest,
        context: TransitionContext,
    ) -> tuple[int, set[str]]:
        nonlocal raised
        result = original(self, manifest, context)
        if not raised:
            raised = True
            message = "interrupted mid-migration"
            raise RuntimeError(message)
        return result

    monkeypatch.setattr(MigrationEngine, "_copy_records", interrupted)

    with pytest.raises(RuntimeError, match="interrupted"):
        _ = store.migrate_canonical(manifest, context(clock, "migration", "corr-migrate"))

    snapshot = store.canonical_records()
    assert snapshot.identities == ()
    assert snapshot.life_records == ()
    assert snapshot.conversation_events == ()
    assert store.applied_manifests() == ()
    assert store.verify_audit_chain().entries_verified == 6

    report = store.migrate_canonical(manifest, context(clock, "migration", "corr-migrate"))
    assert report.copied_records == 5
    assert store.verify_audit_chain().entries_verified == 6 + report.audit_entries_appended
    assert store.read(RecordKind.SOURCE_ITEM, ids["source_item"]) is not None
