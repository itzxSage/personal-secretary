"""Life Engine canonical record contract over the encrypted state boundary.

The Life Engine owns four canonical record families: identity, life,
conversation, and audit. OpenClaw identifiers are never canonical; they appear
only in non-canonical external reference fields such as ``external_id``.
"""

from datetime import datetime
from enum import StrEnum
from typing import ClassVar, Literal, TypeVar, final

from pydantic import ConfigDict, Field, TypeAdapter
from sqlcipher3 import dbapi2 as sqlcipher

from secretary_service.models import FrozenModel, NonEmpty, RecordId


class CanonicalRecordKind(StrEnum):
    """Closed set of Life Engine canonical record families."""

    IDENTITY = "identity"
    LIFE = "life"
    CONVERSATION = "conversation"
    AUDIT = "audit"


class LifeRecordKind(StrEnum):
    """Closed set of canonical life record variants migrated from the foundation."""

    GOAL = "goal"
    TASK = "task"
    EVENT = "event"
    ENERGY_CHECK_IN = "energy_check_in"
    PATTERN_SNAPSHOT = "pattern_snapshot"
    NORMALIZED_FACT = "normalized_fact"


class CanonicalRecordBase(FrozenModel):
    """Fields shared by every immutable canonical record version."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    record_id: RecordId
    created_at: datetime
    state: NonEmpty


class CanonicalIdentity(CanonicalRecordBase):
    """Single canonical Life Engine identity."""

    canonical_kind: Literal[CanonicalRecordKind.IDENTITY] = CanonicalRecordKind.IDENTITY
    display_name: NonEmpty
    is_primary: bool


class LifeRecord(CanonicalRecordBase):
    """Canonical life record (goal, task, event, check-in, snapshot, fact)."""

    canonical_kind: Literal[CanonicalRecordKind.LIFE] = CanonicalRecordKind.LIFE
    life_kind: LifeRecordKind
    title: NonEmpty | None = None
    payload: NonEmpty


class ConversationRecord(CanonicalRecordBase):
    """Canonical conversation owned by the Life Engine."""

    canonical_kind: Literal[CanonicalRecordKind.CONVERSATION] = CanonicalRecordKind.CONVERSATION
    title: NonEmpty | None = None
    participant_ids: tuple[RecordId, ...] = ()


class ConversationEvent(CanonicalRecordBase):
    """Ordered canonical conversation event; external IDs are never canonical."""

    canonical_kind: Literal[CanonicalRecordKind.CONVERSATION] = CanonicalRecordKind.CONVERSATION
    conversation_id: RecordId
    sequence: int = Field(ge=1)
    event_type: NonEmpty
    payload: NonEmpty
    external_id: NonEmpty | None = None


class ChannelBinding(CanonicalRecordBase):
    """Non-canonical external identity binding to one canonical identity."""

    canonical_kind: Literal[CanonicalRecordKind.IDENTITY] = CanonicalRecordKind.IDENTITY
    identity_id: RecordId
    channel: NonEmpty
    external_id: NonEmpty


class CanonicalSnapshot(FrozenModel):
    """Latest live version of every canonical record family."""

    identities: tuple[CanonicalIdentity, ...]
    life_records: tuple[LifeRecord, ...]
    conversations: tuple[ConversationRecord, ...]
    conversation_events: tuple[ConversationEvent, ...]
    channel_bindings: tuple[ChannelBinding, ...]


type CanonicalRecord = (
    CanonicalIdentity | LifeRecord | ConversationRecord | ConversationEvent | ChannelBinding
)

CANONICAL_RECORD_ADAPTER: TypeAdapter[CanonicalRecord] = TypeAdapter(CanonicalRecord)

_T = TypeVar("_T", bound=FrozenModel)


@final
class CanonicalRepository:
    """Read the latest live version of every canonical record family."""

    def __init__(self, connection: sqlcipher.Connection) -> None:
        self._connection = connection

    def snapshot(self) -> CanonicalSnapshot:
        """Return the current canonical record snapshot."""
        return CanonicalSnapshot(
            identities=self._latest("canonical_identities", CanonicalIdentity),
            life_records=self._latest("canonical_life_records", LifeRecord),
            conversations=self._latest("canonical_conversations", ConversationRecord),
            conversation_events=self._latest("canonical_conversation_events", ConversationEvent),
            channel_bindings=self._latest("channel_bindings", ChannelBinding),
        )

    def _latest(self, table: str, model: type[_T]) -> tuple[_T, ...]:
        rows = self._connection.execute(
            f"SELECT content_json FROM {table} current WHERE version=(SELECT max(version) FROM {table} versions WHERE versions.record_id=current.record_id)",  # noqa: E501, S608
        ).fetchall()
        return tuple(model.model_validate_json(str(row[0])) for row in rows)
