import json
from pathlib import Path
from typing import ClassVar, final
from uuid import UUID

from pydantic import ConfigDict

from secretary_service.canonical import CanonicalSnapshot
from secretary_service.keys import DeterministicTestKeyProvider, KeyProvider
from secretary_service.migration import DEFAULT_RECORD_MAPPINGS, MigrationManifest, sign_manifest
from secretary_service.models import (
    ActorId,
    CorrelationId,
    FrozenModel,
    Goal,
    Identity,
    RecordId,
    SourceItem,
    TransitionContext,
)
from secretary_service.storage import EncryptedStateStore
from tests.helpers import FakeClock


class StateFixture(FrozenModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    schema_version: int
    identity_id: UUID
    display_name: str
    goal_id: UUID
    goal_title: str
    source_item_id: UUID
    conversation_id: UUID
    external_id: str


class RecoveryFixture(FrozenModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    audit_chain_valid: bool
    database_key_rotated: bool
    invalid_key_rejected: bool
    production_capabilities_enabled: bool


@final
class RotatedDatabaseKeyProvider:
    def __init__(self, source: KeyProvider, database_key: bytes) -> None:
        self._source = source
        self._database_key = database_key

    def database_key(self) -> bytes:
        return self._database_key

    def audit_key(self) -> bytes:
        return self._source.audit_key()

    def backup_wrapping_key(self) -> bytes:
        return self._source.backup_wrapping_key()

    def connector_secret(self, reference: str) -> str:
        return self._source.connector_secret(reference)


def load_state_fixture(path: Path) -> StateFixture:
    return StateFixture.model_validate_json((path / "state.json").read_text(encoding="utf-8"))


def transition_context(clock: FakeClock, correlation_id: str) -> TransitionContext:
    return TransitionContext(
        actor=ActorId("task-18-drill"),
        correlation_id=CorrelationId(correlation_id),
        occurred_at=clock.now(),
    )


def seed_foundation(
    store: EncryptedStateStore, fixture: StateFixture, clock: FakeClock
) -> MigrationManifest:
    context = transition_context(clock, "task-18-seed")
    store.create(
        Identity(
            record_id=RecordId(fixture.identity_id),
            created_at=clock.now(),
            state="active",
            display_name=fixture.display_name,
        ),
        context,
    )
    store.create(
        Goal(
            record_id=RecordId(fixture.goal_id),
            created_at=clock.now(),
            state="active",
            title=fixture.goal_title,
        ),
        context,
    )
    store.create(
        SourceItem(
            record_id=RecordId(fixture.source_item_id),
            created_at=clock.now(),
            external_id=fixture.external_id,
            raw_content="synthetic task-18 fixture",
            source_type="communication",
        ),
        context,
    )
    keys = DeterministicTestKeyProvider.from_seed(b"task-18-state")
    unsigned = MigrationManifest(
        manifest_id=RecordId(UUID("18000000-0000-0000-0000-000000000002")),
        source_schema_version=fixture.schema_version,
        target_schema_version=2,
        record_mappings=DEFAULT_RECORD_MAPPINGS,
        conversation_id=RecordId(fixture.conversation_id),
        created_at=clock.now(),
    )
    return unsigned.model_copy(update={"manifest_hash": sign_manifest(unsigned, keys.audit_key())})


def golden_projection(snapshot: CanonicalSnapshot) -> str:
    identity = snapshot.identities[0]
    life = snapshot.life_records[0]
    conversation = snapshot.conversations[0]
    event = snapshot.conversation_events[0]
    projection = {
        "conversation": {
            "event_external_id": event.external_id,
            "event_id": str(event.record_id),
            "event_sequence": event.sequence,
            "record_id": str(conversation.record_id),
        },
        "identity": {
            "display_name": identity.display_name,
            "record_id": str(identity.record_id),
            "state": identity.state,
        },
        "life": {
            "kind": life.life_kind.value,
            "record_id": str(life.record_id),
            "state": life.state,
            "title": life.title,
        },
        "production_capabilities_enabled": False,
    }
    return json.dumps(projection, indent=2, sort_keys=True) + "\n"
