"""Typed domain records persisted by the secretary state boundary."""

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, ClassVar, Literal, NewType, override
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter

ActorId = NewType("ActorId", str)
CorrelationId = NewType("CorrelationId", str)
RecordId = NewType("RecordId", UUID)
BackupId = NewType("BackupId", UUID)
NonEmpty = Annotated[str, StringConstraints(min_length=1)]


class FrozenModel(BaseModel):
    """Immutable validated model used at persistence boundaries."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)


class RecordKind(StrEnum):
    """Closed set of persisted domain record variants."""

    IDENTITY = "identity"
    CONNECTOR = "connector"
    CAPABILITY = "capability"
    SOURCE_ITEM = "source_item"
    NORMALIZED_FACT = "normalized_fact"
    TASK = "task"
    EVENT = "event"
    PROPOSAL = "proposal"
    APPROVAL = "approval"
    EXECUTION = "execution"
    ENERGY_CHECK_IN = "energy_check_in"
    GOAL = "goal"
    PATTERN_SNAPSHOT = "pattern_snapshot"
    CONSENT_RECORD = "consent_record"


class BackupStatus(StrEnum):
    """Cryptographic recoverability state of a backup."""

    ACTIVE = "active"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"


class TransitionContext(FrozenModel):
    """Mandatory attribution attached to every state change."""

    actor: Annotated[ActorId, StringConstraints(min_length=1)]
    correlation_id: Annotated[CorrelationId, StringConstraints(min_length=1)]
    occurred_at: datetime


class RecordBase(FrozenModel):
    """Fields shared by every immutable domain version."""

    record_id: RecordId
    created_at: datetime
    state: NonEmpty

    def provenance_source_id(self) -> RecordId | None:
        """Return explicit source provenance when this record has one."""
        return None


class Identity(RecordBase):
    """Single-user identity state."""

    kind: Literal[RecordKind.IDENTITY] = RecordKind.IDENTITY
    display_name: NonEmpty


class Connector(RecordBase):
    """Connector metadata containing only a Keychain reference."""

    kind: Literal[RecordKind.CONNECTOR] = RecordKind.CONNECTOR
    name: NonEmpty
    secret_reference: NonEmpty


class Capability(RecordBase):
    """Persisted connector capability state without policy behavior."""

    kind: Literal[RecordKind.CAPABILITY] = RecordKind.CAPABILITY
    connector_id: RecordId
    name: NonEmpty
    granted: bool


class SourceItem(RecordBase):
    """Encrypted raw communication intake."""

    kind: Literal[RecordKind.SOURCE_ITEM] = RecordKind.SOURCE_ITEM
    state: NonEmpty = "captured"
    source_type: Literal["communication"]
    external_id: NonEmpty
    raw_content: NonEmpty


class NormalizedFact(RecordBase):
    """Typed normalized fact linked to source provenance."""

    kind: Literal[RecordKind.NORMALIZED_FACT] = RecordKind.NORMALIZED_FACT
    source_id: RecordId
    fact_type: NonEmpty
    value: NonEmpty

    @override
    def provenance_source_id(self) -> RecordId:
        """Return the required source provenance identifier."""
        return self.source_id


class Task(RecordBase):
    """Persisted task state without planning behavior."""

    kind: Literal[RecordKind.TASK] = RecordKind.TASK
    title: NonEmpty


class Event(RecordBase):
    """Persisted event state without connector behavior."""

    kind: Literal[RecordKind.EVENT] = RecordKind.EVENT
    title: NonEmpty
    starts_at: datetime


class Proposal(RecordBase):
    """Traceable proposal data awaiting later policy enforcement."""

    kind: Literal[RecordKind.PROPOSAL] = RecordKind.PROPOSAL
    proposal_type: NonEmpty
    payload: NonEmpty
    policy_decision: NonEmpty
    provider_version: NonEmpty
    reversible: bool
    approval_state: NonEmpty


class Approval(RecordBase):
    """Approval fact without Todo 3 authorization semantics."""

    kind: Literal[RecordKind.APPROVAL] = RecordKind.APPROVAL
    proposal_id: RecordId
    decision: NonEmpty


class Execution(RecordBase):
    """Execution outcome fact without executor behavior."""

    kind: Literal[RecordKind.EXECUTION] = RecordKind.EXECUTION
    proposal_id: RecordId
    outcome: NonEmpty


class EnergyCheckIn(RecordBase):
    """Encrypted raw health/profile check-in."""

    kind: Literal[RecordKind.ENERGY_CHECK_IN] = RecordKind.ENERGY_CHECK_IN
    state: NonEmpty = "recorded"
    energy: int = Field(ge=1, le=5)
    mood: NonEmpty


class Goal(RecordBase):
    """Persisted user goal state."""

    kind: Literal[RecordKind.GOAL] = RecordKind.GOAL
    title: NonEmpty


class PatternSnapshot(RecordBase):
    """Aggregated de-identified pattern snapshot."""

    kind: Literal[RecordKind.PATTERN_SNAPSHOT] = RecordKind.PATTERN_SNAPSHOT
    label: NonEmpty
    sample_count: int = Field(ge=0)


class ConsentRecord(RecordBase):
    """Persisted consent fact without policy enforcement."""

    kind: Literal[RecordKind.CONSENT_RECORD] = RecordKind.CONSENT_RECORD
    scope: NonEmpty
    granted: bool


class Tombstone(FrozenModel):
    """Content-free proof of user deletion."""

    record_id: RecordId
    record_kind: RecordKind
    deleted_at: datetime
    source_fingerprint: str


class BackupManifest(FrozenModel):
    """Durable backup envelope metadata."""

    backup_id: BackupId
    path: Path
    created_at: datetime
    expires_at: datetime
    wrapped_data_key: bytes | None
    nonce: bytes | None
    status: BackupStatus


type DomainRecord = (
    Identity
    | Connector
    | Capability
    | SourceItem
    | NormalizedFact
    | Task
    | Event
    | Proposal
    | Approval
    | Execution
    | EnergyCheckIn
    | Goal
    | PatternSnapshot
    | ConsentRecord
)

DOMAIN_RECORD_ADAPTER: TypeAdapter[DomainRecord] = TypeAdapter(DomainRecord)


def record_kind(record: DomainRecord) -> RecordKind:
    """Return the closed record variant's storage kind."""
    return record.kind


def source_reference(record: DomainRecord) -> RecordId | None:
    """Return explicit source provenance for normalized content."""
    return record.provenance_source_id()
