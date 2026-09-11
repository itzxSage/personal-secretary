"""Typed governed learning records and lifecycle requests."""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, TypeAdapter

from secretary_service.memory import MemoryCategory
from secretary_service.models import FrozenModel, NonEmpty, RecordId


class LearningSource(StrEnum):
    """Origin classes retained as learning provenance."""

    PLANNER = "planner"
    CONVERSATION = "conversation"
    USER_STATEMENT = "user_statement"
    USER_CORRECTION = "user_correction"


class ActualOutcome(StrEnum):
    """Closed set of observed planned-activity outcomes."""

    COMPLETED = "completed"
    ABANDONED = "abandoned"
    RESCHEDULED = "rescheduled"
    DEFERRED = "deferred"


class ProposalKind(StrEnum):
    """Closed variants of explainable learning proposals."""

    PROCEDURE = "procedure"
    MEMORY_UPDATE = "memory_update"


class ProposalStatus(StrEnum):
    """Governed lifecycle states of a learning proposal."""

    PROPOSED = "proposed"
    APPROVED = "approved"
    CORRECTED = "corrected"
    DELETED = "deleted"


class LearningProvenance(FrozenModel):
    """Traceable source metadata for a learning observation."""

    source: LearningSource
    source_id: NonEmpty
    captured_at: datetime


class PlannedActivity(FrozenModel):
    """Planned activity snapshot captured before execution."""

    activity_id: RecordId
    title: NonEmpty
    planned_start: datetime
    planned_end: datetime
    energy_required: int | None = Field(default=None, ge=1, le=5)


class ObservationTrigger(FrozenModel):
    """Stable signal that identifies a repeatable situation."""

    kind: NonEmpty
    source: NonEmpty


class LearningObservation(FrozenModel):
    """One planned-vs-actual outcome with optional procedure evidence."""

    observation_id: RecordId
    planned: PlannedActivity
    outcome: ActualOutcome
    actual_start: datetime | None = None
    actual_end: datetime | None = None
    trigger: ObservationTrigger | None = None
    procedure_steps: tuple[NonEmpty, ...] = ()
    context_tags: tuple[NonEmpty, ...] = ()
    provenance: LearningProvenance
    observed_at: datetime
    retain_until: datetime | None = None


class SkillProposal(FrozenModel):
    """Explainable suggested procedure awaiting explicit approval."""

    proposal_id: RecordId
    kind: Literal[ProposalKind.PROCEDURE] = ProposalKind.PROCEDURE
    trigger: ObservationTrigger
    procedure: tuple[NonEmpty, ...]
    supporting_observation_ids: tuple[RecordId, ...]
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: NonEmpty
    status: ProposalStatus = ProposalStatus.PROPOSED
    created_at: datetime
    corrected_at: datetime | None = None
    correction_note: NonEmpty | None = None


class MemoryUpdateProposal(FrozenModel):
    """Explainable suggested memory update awaiting explicit approval."""

    proposal_id: RecordId
    kind: Literal[ProposalKind.MEMORY_UPDATE] = ProposalKind.MEMORY_UPDATE
    category: MemoryCategory
    content: NonEmpty
    supporting_observation_ids: tuple[RecordId, ...]
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: NonEmpty
    status: ProposalStatus = ProposalStatus.PROPOSED
    created_at: datetime
    corrected_at: datetime | None = None
    correction_note: NonEmpty | None = None


type LearningProposal = SkillProposal | MemoryUpdateProposal
LEARNING_PROPOSAL_ADAPTER: TypeAdapter[LearningProposal] = TypeAdapter(LearningProposal)


class ProposalCorrection(FrozenModel):
    """User-authored proposal correction guarded by expected status."""

    expected_status: ProposalStatus
    note: NonEmpty
    procedure: tuple[NonEmpty, ...] | None = None
    content: NonEmpty | None = None


class ProposalDeletion(FrozenModel):
    """User deletion request guarded by expected status."""

    expected_status: ProposalStatus


class LearningMetrics(FrozenModel):
    """Content-free learning observability counters."""

    observation_count: int = Field(ge=0)
    proposal_count: int = Field(ge=0)
    procedure_proposals: int = Field(ge=0)
    memory_update_proposals: int = Field(ge=0)
    proposed: int = Field(ge=0)
    approved: int = Field(ge=0)
    corrected: int = Field(ge=0)
    deleted: int = Field(ge=0)
    mean_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
