"""Exact explainable output schema for proposed-day planning."""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field, computed_field

from secretary_service.planner_models import NonEmptyText, PlannerModel, ScheduleResolution


class PlanStatus(StrEnum):
    """Overall feasibility of a proposed day."""

    FEASIBLE = "feasible"
    PARTIAL = "partial"
    INFEASIBLE = "infeasible"


class PlanBlockKind(StrEnum):
    """Minute-level internal plan block type."""

    FIXED = "fixed"
    FLEXIBLE = "flexible"
    PROTECTED = "protected"
    TRAVEL = "travel"
    AVAILABLE = "available"


class ExplanationCode(StrEnum):
    """Machine-readable planning rationale category."""

    FIXED = "fixed"
    PROTECTED = "protected"
    SCHEDULED = "scheduled"
    OVERDUE = "overdue"
    INFEASIBLE = "infeasible"


class UnscheduledReason(StrEnum):
    """Machine-readable reason an activity could not be placed."""

    FIXED_CONFLICT = "fixed-conflict"
    MISSING_DEPENDENCY = "missing-dependency"
    DEPENDENCY_UNSCHEDULED = "dependency-unscheduled"
    NO_CAPACITY = "no-capacity"


class ScoreBreakdown(PlannerModel):
    """Every deterministic score component used for ordering and placement."""

    deadline: int
    dependency: int
    travel: int
    energy: int
    goal: int
    importance: int
    total: int


class PlanBlock(PlannerModel):
    """Contiguous minute-level internal allocation."""

    block_id: NonEmptyText
    activity_id: NonEmptyText | None
    title: NonEmptyText
    kind: PlanBlockKind
    starts_at: datetime
    ends_at: datetime
    display_group: NonEmptyText | None = None
    display_title: NonEmptyText | None = None
    guidance: tuple[NonEmptyText, ...] = ()
    deadline: datetime | None = None
    explanation_code: ExplanationCode | None = None
    calendar_eligible: bool = False
    personal_micro_event: bool = False

    @computed_field
    @property
    def duration_minutes(self) -> int:
        """Return real elapsed minutes, including across DST transitions."""
        return int(
            (self.ends_at.astimezone(UTC) - self.starts_at.astimezone(UTC)).total_seconds() // 60
        )


class CalendarBlock(PlannerModel):
    """Human-readable projection preserving actionable source context."""

    block_id: NonEmptyText
    display_group: NonEmptyText
    title: NonEmptyText
    starts_at: datetime
    ends_at: datetime
    source_activity_ids: tuple[NonEmptyText, ...]
    segment_titles: tuple[NonEmptyText, ...]
    transitions: tuple[NonEmptyText, ...]
    deadlines: tuple[datetime, ...]
    guidance: tuple[NonEmptyText, ...]
    explanation_codes: tuple[ExplanationCode, ...]


class TaskExplanation(PlannerModel):
    """Exact machine and human rationale for one activity decision."""

    activity_id: NonEmptyText
    code: ExplanationCode
    summary: NonEmptyText
    score: ScoreBreakdown
    scheduled_block_ids: tuple[NonEmptyText, ...]
    constraints: tuple[NonEmptyText, ...]


class UnscheduledActivity(PlannerModel):
    """Explicit infeasibility detail for one activity."""

    activity_id: NonEmptyText
    reason: UnscheduledReason
    detail: NonEmptyText
    blocking_activity_ids: tuple[NonEmptyText, ...] = ()


class BlockChange(PlannerModel):
    """Added or removed schedule block in an exact proposal diff."""

    block_id: NonEmptyText
    activity_id: NonEmptyText
    title: NonEmptyText
    starts_at: datetime
    ends_at: datetime


class MovedBlock(PlannerModel):
    """Before and after interval for a moved schedule block."""

    block_id: NonEmptyText
    activity_id: NonEmptyText
    title: NonEmptyText
    previous_starts_at: datetime
    previous_ends_at: datetime
    proposed_starts_at: datetime
    proposed_ends_at: datetime


class PlanDiff(PlannerModel):
    """Stable block-level changes from the supplied baseline."""

    added: tuple[BlockChange, ...] = ()
    removed: tuple[BlockChange, ...] = ()
    moved: tuple[MovedBlock, ...] = ()


class ProposedDay(PlannerModel):
    """Explainable and non-mutating proposed-day result."""

    schema_version: str = "lifeos.proposed-day.v1"
    input_fingerprint: NonEmptyText
    state_revision: int = Field(ge=1)
    timezone: NonEmptyText
    status: PlanStatus
    schedule_resolution: ScheduleResolution
    internal_plan: tuple[PlanBlock, ...]
    calendar_projection: tuple[CalendarBlock, ...]
    explanations: tuple[TaskExplanation, ...]
    unscheduled: tuple[UnscheduledActivity, ...]
    diff: PlanDiff
