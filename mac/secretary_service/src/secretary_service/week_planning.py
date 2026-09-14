"""Governed deterministic week planning from structured Life Model knowledge.

# noqa: SIZE_OK - required service and wire models are confined to the two user-scoped files.

Routine occurrences use stable memory/date activity IDs. Fixed logistics expand
the protected interval: preparation and travel precede the stated start, while
transition follows its duration. Movable logistics use planner travel
reservations. Routine priority maps to planner goal weight; optional routines
use zero. Projects, unstructured knowledge, unresolved dependencies, and
unrepresentable routines remain explicit ``unknown_time`` gaps.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import ClassVar, Final, Literal, final, override
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ConfigDict, TypeAdapter

from secretary_service.authority import (
    Approval,
    PolicyViolationError,
    ProposalLifecycle,
    ProposalRecord,
    ProposalState,
    default_approval_matrix,
)
from secretary_service.enrollment import DeviceRegistry
from secretary_service.google_calendar import GoogleCalendarAdapter
from secretary_service.google_calendar_contract import (
    CalendarEventDraft,
    CalendarOperation,
    CalendarPlan,
    CalendarPreview,
    calendar_payload,
)
from secretary_service.google_calendar_errors import (
    CalendarAuthorizationError,
    CalendarContractError,
)
from secretary_service.leases import LeaseIssuer, LeaseRequest, LeaseViolationError, SigningKeyRing
from secretary_service.life_knowledge import RoutineDetails, RoutineFlexibility
from secretary_service.life_model import KnowledgeView, LifeModel
from secretary_service.models import (
    ActorId,
    CorrelationId,
    FrozenModel,
    NonEmpty,
    Proposal,
    RecordId,
    RecordKind,
    TransitionContext,
)
from secretary_service.planner import propose_week
from secretary_service.planner_models import ActivityFlexibility, PlanActivity, WeekPlanRequest
from secretary_service.planner_results import (
    CalendarBlock,
    PlanBlock,
    PlanStatus,
    TaskExplanation,
    UnscheduledActivity,
)
from secretary_service.storage import Clock, EncryptedStateStore

ACTION: Final = "calendar.apply"
WORKER: Final = "lifeos-calendar-worker"
ACTOR: Final = ActorId("lifeos")
CORRELATION: Final = CorrelationId("week-planning")
EVENTS = TypeAdapter(tuple[CalendarEventDraft, ...])
DEVICE_MISMATCH: Final = "device_mismatch"
APPROVAL_REJECTED: Final = "approval_rejected"
LEASE_REJECTED: Final = "lease_rejected"
CALENDAR_AUTHORIZATION: Final = "calendar_authorization"
CALENDAR_CONTRACT: Final = "calendar_contract"


class BoundaryModel(FrozenModel):
    """Strict immutable week-planning wire model."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class PlanningGap(BoundaryModel):
    """Structured knowledge that cannot safely become a timed activity."""

    memory_id: RecordId
    title: NonEmpty
    reason: Literal["unknown_time"]
    explanation: NonEmpty


class WeekPlanProposal(BoundaryModel):
    """Deterministic plan and read-only calendar preview awaiting approval."""

    proposal_id: RecordId
    payload_hash: NonEmpty
    status: PlanStatus
    plan_date: date
    timezone: str
    window_start: datetime
    window_end: datetime
    blocks: tuple[PlanBlock, ...]
    calendar_blocks: tuple[CalendarBlock, ...]
    explanations: tuple[TaskExplanation, ...]
    unscheduled: tuple[UnscheduledActivity, ...]
    gaps: tuple[PlanningGap, ...]
    calendar_dry_run: CalendarPreview


class WeekPlanApproval(BoundaryModel):
    """Device-signed approval wire envelope."""

    approval: Approval


class WeekPlanExecutionResult(BoundaryModel):
    """Successful one-shot calendar execution summary."""

    proposal_id: RecordId
    state: ProposalState
    lease_id: RecordId
    applied_operations: int
    message: NonEmpty


class WeekPlanningError(Exception):
    """Base error translated by the week-planning transport boundary."""


@dataclass(frozen=True, slots=True)
class WeekPlanProposalError(WeekPlanningError):
    """The durable proposal is absent, malformed, or no longer approvable."""

    proposal_id: RecordId
    reason: Literal["unknown_proposal", "invalid_proposal", "stale_proposal"]

    @override
    def __str__(self) -> str:
        """Return a content-free machine reason and proposal identifier."""
        return f"{self.reason}: {self.proposal_id}"


@dataclass(frozen=True, slots=True)
class WeekPlanningPolicyError(WeekPlanningError):
    """Approval or lease policy rejected execution."""

    reason: Literal["device_mismatch", "approval_rejected", "lease_rejected"]

    @override
    def __str__(self) -> str:
        """Return the machine-readable policy reason."""
        return self.reason


@dataclass(frozen=True, slots=True)
class WeekPlanningProviderError(WeekPlanningError):
    """Calendar authorization or contract execution failed."""

    reason: Literal["calendar_authorization", "calendar_contract"]

    @override
    def __str__(self) -> str:
        """Return the machine-readable provider reason."""
        return self.reason


@dataclass(frozen=True, slots=True)
class _RoutineOccurrence:
    """One source routine occurrence before dependency resolution."""

    view: KnowledgeView
    routine: RoutineDetails
    local_date: date
    activity_id: str


@final
class WeekPlanningService:
    """Preview structured knowledge and apply only approved bound payloads."""

    def __init__(  # noqa: PLR0913, PLR0917 - required orchestration dependencies
        self,
        clock: Clock,
        devices: DeviceRegistry,
        store: EncryptedStateStore,
        calendar: GoogleCalendarAdapter,
        signing_key: bytes,
        lease_ttl: timedelta,
    ) -> None:
        """Bind governance, persistence, planner, and provider dependencies."""
        self._clock = clock
        self._store = store
        self._calendar = calendar
        self._lifecycle = ProposalLifecycle(clock, devices, store.authority_consumption)
        self._leases = LeaseIssuer(clock, SigningKeyRing(signing_key), lease_ttl)

    def preview(self, subject_id: str, now: datetime, timezone: str) -> WeekPlanProposal:
        """Persist a payload-bound proposal after a mutation-free calendar preview."""
        zone = ZoneInfo(timezone)
        window_start = now.astimezone(zone).replace(hour=0, minute=0, second=0, microsecond=0)
        window_end = (window_start.astimezone(UTC) + timedelta(days=7)).astimezone(zone)
        activities, gaps = self._map_knowledge(
            LifeModel(self._store.memory).planning_knowledge(subject_id, now),
            window_start,
            zone,
        )
        request = WeekPlanRequest(
            plan_date=window_start.date(),
            timezone=timezone,
            window_start=window_start,
            window_end=window_end,
            state_revision=1,
            expected_state_revision=1,
            activities=activities,
        )
        proposed = propose_week(request)
        proposal_id = RecordId(uuid4())
        events = tuple(
            CalendarEventDraft(
                event_key=f"week_{proposal_id.hex}_{index}",
                summary=block.display_group or block.title,
                starts_at=block.starts_at,
                ends_at=block.ends_at,
            )
            for index, block in enumerate(proposed.calendar_projection)
        )
        authorization = ProposalRecord(
            proposal_id=proposal_id,
            action_class=ACTION,
            payload=calendar_payload(events),
            state=ProposalState.PROPOSED,
            created_at=now,
        )
        self._store.create(
            Proposal(
                record_id=proposal_id,
                created_at=now,
                state=ProposalState.PROPOSED.value,
                proposal_type=ACTION,
                payload=authorization.payload,
                policy_decision="approval-required",
                provider_version="google.calendar.v3",
                reversible=True,
                approval_state=ProposalState.PROPOSED.value,
            ),
            TransitionContext(actor=ACTOR, correlation_id=CORRELATION, occurred_at=now),
        )
        dry_run = self._calendar.dry_run(CalendarPlan(authorization=authorization, events=events))
        return WeekPlanProposal(
            proposal_id=proposal_id,
            payload_hash=authorization.payload_hash(),
            status=proposed.status,
            plan_date=request.plan_date,
            timezone=timezone,
            window_start=window_start,
            window_end=window_end,
            blocks=proposed.internal_plan,
            calendar_blocks=proposed.calendar_projection,
            explanations=proposed.explanations,
            unscheduled=proposed.unscheduled,
            gaps=gaps,
            calendar_dry_run=dry_run,
        )

    def approve_and_apply(
        self,
        proposal_id: RecordId,
        approval: Approval,
        context: TransitionContext,
        expected_device_id: UUID,
    ) -> WeekPlanExecutionResult:
        """Apply the persisted payload under device approval and an internal lease."""
        record = self._store.read(RecordKind.PROPOSAL, proposal_id)
        if record is None:
            raise WeekPlanProposalError(proposal_id, "unknown_proposal")
        if not isinstance(record, Proposal) or record.proposal_type != ACTION:
            raise WeekPlanProposalError(proposal_id, "invalid_proposal")
        if record.state != ProposalState.PROPOSED.value:
            raise WeekPlanProposalError(proposal_id, "stale_proposal")
        if approval.device_id is None or str(approval.device_id) != str(expected_device_id):
            raise WeekPlanningPolicyError(DEVICE_MISMATCH)
        proposal = ProposalRecord(
            proposal_id=record.record_id,
            action_class=record.proposal_type,
            payload=record.payload,
            state=ProposalState.PROPOSED,
            created_at=record.created_at,
        )
        try:
            approved_state = self._lifecycle.approve(proposal, approval, default_approval_matrix())
        except PolicyViolationError as error:
            raise WeekPlanningPolicyError(APPROVAL_REJECTED) from error
        approved = proposal.model_copy(update={"state": approved_state})
        self._store.transition(RecordKind.PROPOSAL, proposal_id, approved_state.value, context)
        try:
            lease = self._leases.issue(
                approved,
                LeaseRequest(
                    capability=ACTION,
                    worker_id=WORKER,
                    actor=ACTOR,
                    correlation_id=CORRELATION,
                    idempotency_key=str(approval.fact_id),
                ),
            )
            _ = self._leases.verify(lease, approved)
        except (LeaseViolationError, PolicyViolationError) as error:
            raise WeekPlanningPolicyError(LEASE_REJECTED) from error
        plan = CalendarPlan(authorization=approved, events=EVENTS.validate_json(record.payload))
        try:
            result = self._calendar.apply(plan)
        except CalendarAuthorizationError as error:
            raise WeekPlanningProviderError(CALENDAR_AUTHORIZATION) from error
        except CalendarContractError as error:
            raise WeekPlanningProviderError(CALENDAR_CONTRACT) from error
        applied_state = self._lifecycle.apply(approved, lease)
        self._store.transition(RecordKind.PROPOSAL, proposal_id, applied_state.value, context)
        return WeekPlanExecutionResult(
            proposal_id=proposal_id,
            state=applied_state,
            lease_id=lease.fact_id,
            applied_operations=sum(
                operation.operation is not CalendarOperation.NOOP for operation in result.operations
            ),
            message="Week plan applied to the LifeOS Proposed calendar.",
        )

    @staticmethod
    def _map_knowledge(
        views: tuple[KnowledgeView, ...], window_start: datetime, planning_zone: ZoneInfo
    ) -> tuple[tuple[PlanActivity, ...], tuple[PlanningGap, ...]]:
        occurrences: dict[RecordId, tuple[_RoutineOccurrence, ...]] = {}
        gaps: dict[RecordId, PlanningGap] = {}
        for view in sorted(views, key=lambda item: str(item.record.memory_id)):
            details = view.record.knowledge
            if details is None or details.routine is None:
                gaps[view.record.memory_id] = _gap(view)
                continue
            routine = details.routine
            try:
                routine_zone = ZoneInfo(routine.timezone)
            except ZoneInfoNotFoundError:
                gaps[view.record.memory_id] = _gap(view)
                continue
            first_date = window_start.astimezone(routine_zone).date()
            dates = tuple(
                first_date + timedelta(days=offset)
                for offset in range(7)
                if (first_date + timedelta(days=offset)).weekday() in routine.days
            )
            selected = (
                dates[: routine.minimum_weekly_frequency]
                if routine.minimum_weekly_frequency
                else dates
            )
            occurrences[view.record.memory_id] = tuple(
                _RoutineOccurrence(
                    view=view,
                    routine=routine,
                    local_date=local_date,
                    activity_id=f"routine:{view.record.memory_id}:{local_date.isoformat()}",
                )
                for local_date in selected
            )
        activities: list[PlanActivity] = []
        for memory_id, items in occurrences.items():
            dependencies = items[0].routine.dependency_ids if items else ()
            if any(dependency not in occurrences for dependency in dependencies):
                if items:
                    gaps[memory_id] = _gap(items[0].view)
                continue
            dependency_ids = tuple(
                occurrence.activity_id
                for dependency in dependencies
                for occurrence in occurrences[dependency]
            )
            activities.extend(_activity(item, dependency_ids, planning_zone) for item in items)
        return tuple(activities), tuple(gaps.values())


def _gap(view: KnowledgeView) -> PlanningGap:
    return PlanningGap(
        memory_id=view.record.memory_id,
        title=view.record.content,
        reason="unknown_time",
        explanation="Structured knowledge does not provide a safely schedulable time.",
    )


def _activity(
    occurrence: _RoutineOccurrence, dependencies: tuple[str, ...], planning_zone: ZoneInfo
) -> PlanActivity:
    routine = occurrence.routine
    nominal = (
        None
        if routine.start_time is None
        else datetime.combine(occurrence.local_date, routine.start_time, ZoneInfo(routine.timezone))
    )
    if routine.flexibility is RoutineFlexibility.FIXED:
        if nominal is None:
            message = "validated fixed routine is missing a start time"
            raise AssertionError(message)
        fixed_start = (
            nominal.astimezone(UTC)
            - timedelta(minutes=routine.preparation_minutes + routine.travel_minutes)
        ).astimezone(planning_zone)
        fixed_end = (
            nominal.astimezone(UTC)
            + timedelta(minutes=routine.duration_minutes + routine.transition_minutes)
        ).astimezone(planning_zone)
        return PlanActivity(
            activity_id=occurrence.activity_id,
            title=occurrence.view.record.content,
            flexibility=ActivityFlexibility.FIXED,
            duration_minutes=int(
                (fixed_end.astimezone(UTC) - fixed_start.astimezone(UTC)).total_seconds() // 60
            ),
            fixed_start=fixed_start,
            fixed_end=fixed_end,
            dependencies=dependencies,
            display_group=occurrence.view.record.content,
            calendar_eligible=False,
        )
    weight = 0 if routine.flexibility is RoutineFlexibility.OPTIONAL else routine.priority
    return PlanActivity(
        activity_id=occurrence.activity_id,
        title=occurrence.view.record.content,
        flexibility=ActivityFlexibility.FLEXIBLE,
        duration_minutes=routine.duration_minutes,
        earliest_start=None if nominal is None else nominal.astimezone(planning_zone),
        dependencies=dependencies,
        travel_minutes_before=routine.preparation_minutes + routine.travel_minutes,
        travel_minutes_after=routine.transition_minutes,
        goal_weight=weight,
        display_group=occurrence.view.record.content,
        calendar_eligible=True,
    )
