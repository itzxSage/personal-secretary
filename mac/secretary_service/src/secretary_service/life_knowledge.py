"""Structured, temporal Life Model metadata over governed canonical memory."""

from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from typing import ClassVar, Literal, Self
from zoneinfo import ZoneInfo

from pydantic import ConfigDict, Field, model_validator

from secretary_service.models import FrozenModel, NonEmpty, RecordId


class KnowledgeState(StrEnum):
    """Evidence state, never an execution permission."""

    CONFIRMED = "confirmed"
    OBSERVED = "observed"
    INFERRED = "inferred"
    STALE = "stale"
    CONFLICTED = "conflicted"
    UNKNOWN = "unknown"


class KnowledgeKind(StrEnum):
    """Purpose of one atomic assertion, rather than a generated biography."""

    IDENTITY = "identity"
    FACT = "fact"
    PATTERN = "pattern"
    PREFERENCE = "preference"
    CURRENT_STATE = "current_state"
    HISTORY = "history"
    ASPIRATION = "aspiration"
    COMMITMENT = "commitment"
    ROUTINE = "routine"
    RELATIONSHIP = "relationship"
    PROJECT = "project"
    OPEN_LOOP = "open_loop"
    CONSTRAINT = "constraint"
    VALUE = "value"
    DECISION_PREFERENCE = "decision_preference"
    COMMUNICATION = "communication"
    ENERGY = "energy"
    PLACE = "place"
    LIFE_DOMAIN = "life_domain"
    QUESTION = "question"
    ANTI_GOAL = "anti_goal"
    TRUST_PREFERENCE = "trust_preference"


class LifeDomain(StrEnum):
    """User-facing areas of understanding and interview progress."""

    IDENTITY = "identity"
    RESPONSIBILITIES = "responsibilities"
    WORK = "work"
    FINANCES = "finances"
    EDUCATION = "education"
    RELATIONSHIPS = "relationships"
    VALUES = "values"
    GOALS = "goals"
    ROUTINES = "routines"
    ENERGY = "energy"
    WELLNESS = "wellness"
    HABITS = "habits"
    LOGISTICS = "logistics"
    PROJECTS = "projects"
    OPEN_LOOPS = "open_loops"
    NOW = "now"
    PLANNING = "planning"
    AUTONOMY = "autonomy"
    COMMUNICATION = "communication"
    STRESS = "stress"
    ANTI_GOALS = "anti_goals"
    TRUST = "trust"


class Sensitivity(StrEnum):
    """Purpose-limited personal data classification."""

    PERSONAL = "personal"
    SENSITIVE = "sensitive"
    RESTRICTED = "restricted"


class RoutineFlexibility(StrEnum):
    """Scheduling preference; this grants no rescheduling authority."""

    FIXED = "fixed"
    PREFERRED = "preferred"
    FLEXIBLE = "flexible"
    OPTIONAL = "optional"


class KnowledgeModel(FrozenModel):
    """Versioned strict domain models with explicit extension points."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class RoutineDetails(KnowledgeModel):
    """Actual or desired recurring time, including preparation and logistics."""

    days: frozenset[int] = Field(min_length=1)
    start_time: time | None = None
    duration_minutes: int = Field(ge=1, le=1440)
    timezone: NonEmpty
    flexibility: RoutineFlexibility
    desired: bool = False
    location_id: RecordId | None = None
    travel_minutes: int = Field(default=0, ge=0, le=1440)
    preparation_minutes: int = Field(default=0, ge=0, le=1440)
    transition_minutes: int = Field(default=0, ge=0, le=1440)
    minimum_weekly_frequency: int = Field(default=0, ge=0, le=7)
    priority: int = Field(default=5, ge=0, le=10)
    can_move: bool = False
    dependency_ids: tuple[RecordId, ...] = ()

    @model_validator(mode="after")
    def validate_recurrence(self) -> Self:
        """Reject ambiguous recurrence shapes before they can influence planning."""
        _ = ZoneInfo(self.timezone)
        if not self.days <= set(range(7)):
            msg = "routine days must use Monday=0 through Sunday=6"
            raise ValueError(msg)
        if self.minimum_weekly_frequency > len(self.days):
            msg = "minimum frequency exceeds permitted days"
            raise ValueError(msg)
        if self.flexibility is RoutineFlexibility.FIXED and (
            self.start_time is None or self.can_move
        ):
            msg = "fixed routines require a start time and cannot move"
            raise ValueError(msg)
        if self.start_time is not None and (
            self.start_time.tzinfo is not None
            or self.start_time.second
            or self.start_time.microsecond
        ):
            msg = "routine start uses local minute resolution and a separate timezone"
            raise ValueError(msg)
        return self


class ProjectDetails(KnowledgeModel):
    """Inventory before task generation, optionally linked to the existing goal graph."""

    desired_outcome: NonEmpty
    why_it_matters: str | None = None
    current_state: NonEmpty = "inbox"
    deadline: datetime | None = None
    next_action: str | None = None
    blocker: str | None = None
    dependency_ids: tuple[RecordId, ...] = ()
    people_ids: tuple[RecordId, ...] = ()
    resource_ids: tuple[RecordId, ...] = ()
    goal_graph_id: RecordId | None = None
    active: bool | None = None


class OpenLoopDetails(KnowledgeModel):
    """Inbox capture that is deliberately ineligible for automatic scheduling."""

    disposition: Literal["inbox", "clarified", "deferred", "someday", "delegated", "done"] = "inbox"
    actionable: bool | None = None
    desired_outcome: str | None = None
    next_action: str | None = None
    deadline: datetime | None = None
    duration_minutes: int | None = Field(default=None, ge=1)
    energy_required: int | None = Field(default=None, ge=1, le=5)
    context: str | None = None
    dependency_ids: tuple[RecordId, ...] = ()
    delegate_id: RecordId | None = None


def require_aware(value: datetime) -> None:
    """Temporal knowledge must describe instants without local-time ambiguity."""
    if value.tzinfo is None or value.utcoffset() is None:
        msg = "knowledge timestamps must be timezone-aware"
        raise ValueError(msg)


class KnowledgeDetails(KnowledgeModel):
    """One subject/predicate assertion with explicit evidence and validity."""

    schema_version: Literal[1] = 1
    subject_id: NonEmpty
    key: NonEmpty
    cardinality: Literal["one", "many"] = "one"
    domain: LifeDomain
    kind: KnowledgeKind
    state: KnowledgeState
    observed_at: datetime
    last_confirmed_at: datetime | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    expected_staleness_days: int | None = Field(default=None, ge=1)
    sensitivity: Sensitivity = Sensitivity.PERSONAL
    importance: int = Field(default=5, ge=0, le=10)
    related_entity_ids: tuple[RecordId, ...] = ()
    related_goal_ids: tuple[RecordId, ...] = ()
    related_project_ids: tuple[RecordId, ...] = ()
    evidence_ids: tuple[RecordId, ...] = ()
    conflicts_with: tuple[RecordId, ...] = ()
    planning_allowed: bool = False
    routine: RoutineDetails | None = None
    project: ProjectDetails | None = None
    open_loop: OpenLoopDetails | None = None

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        """Keep unknown, historical, inferred and confirmed assertions distinct."""
        for value in (self.observed_at, self.last_confirmed_at, self.valid_from, self.valid_until):
            if value is not None:
                require_aware(value)
        if self.valid_from and self.valid_until and self.valid_until <= self.valid_from:
            msg = "knowledge validity must end after it starts"
            raise ValueError(msg)
        if self.state is KnowledgeState.CONFIRMED and self.last_confirmed_at is None:
            msg = "confirmed knowledge requires explicit confirmation time"
            raise ValueError(msg)
        if (
            self.state in {KnowledgeState.INFERRED, KnowledgeState.UNKNOWN}
            and self.last_confirmed_at
        ):
            msg = "inferences and unknowns cannot claim user confirmation"
            raise ValueError(msg)
        if self.conflicts_with and self.state is not KnowledgeState.CONFLICTED:
            msg = "contradictory evidence must remain conflicted"
            raise ValueError(msg)
        for details, kind in (
            (self.routine, KnowledgeKind.ROUTINE),
            (self.project, KnowledgeKind.PROJECT),
            (self.open_loop, KnowledgeKind.OPEN_LOOP),
        ):
            if details is not None and self.kind is not kind:
                msg = "structured details must match the knowledge kind"
                raise ValueError(msg)
        return self

    def current_at(self, now: datetime) -> bool:
        """Separate historical truth from facts applicable to the current decision."""
        require_aware(now)
        return (self.valid_from is None or self.valid_from <= now) and (
            self.valid_until is None or now < self.valid_until
        )

    def effective_state(self, now: datetime) -> KnowledgeState:
        """Derive staleness without mutating evidence or silently resolving conflict."""
        require_aware(now)
        if self.state in {KnowledgeState.CONFLICTED, KnowledgeState.UNKNOWN, KnowledgeState.STALE}:
            return self.state
        reference = self.last_confirmed_at or self.observed_at
        if self.expected_staleness_days is not None and now.astimezone(UTC) >= (
            reference.astimezone(UTC) + timedelta(days=self.expected_staleness_days)
        ):
            return KnowledgeState.STALE
        return self.state


class InterviewProgress(KnowledgeModel):
    """Resumable interview position; answers live only in governed assertions."""

    schema_version: Literal[1] = 1
    subject_id: NonEmpty
    phase: Literal["active", "paused", "continuous"] = "active"
    skipped_keys: frozenset[str] = frozenset()
    permitted_domains: frozenset[LifeDomain] = frozenset()
    skipped_domains: frozenset[LifeDomain] = frozenset()
    last_question_key: str | None = None
    sweep_complete: bool = False
