"""Pure deterministic minute-resolution proposed-day planner."""

from dataclasses import dataclass
from hashlib import sha256
from typing import override

from secretary_service.planner_explanation import (
    build_internal_plan,
    explain_activity,
    infeasible_explanation,
)
from secretary_service.planner_models import DayPlanRequest
from secretary_service.planner_placement import (
    dependent_counts,
    invalid_dependencies,
    place_fixed,
    place_flexible,
)
from secretary_service.planner_projection import build_diff, project_calendar
from secretary_service.planner_results import (
    PlanStatus,
    ProposedDay,
    UnscheduledActivity,
    UnscheduledReason,
)


@dataclass(frozen=True, slots=True)
class StalePlannerStateError(Exception):
    """The request's expected canonical revision is no longer current."""

    expected_revision: int
    current_revision: int

    @override
    def __str__(self) -> str:
        """Return both revisions without including proposal content."""
        return (
            f"expected revision {self.expected_revision}, current revision {self.current_revision}"
        )


def propose_day(request: DayPlanRequest) -> ProposedDay:
    """Produce one explainable plan without mutating canonical or calendar state."""
    if request.expected_state_revision != request.state_revision:
        raise StalePlannerStateError(
            expected_revision=request.expected_state_revision,
            current_revision=request.state_revision,
        )
    fingerprint = sha256(
        request.model_dump_json(exclude={"expected_state_revision"}).encode()
    ).hexdigest()
    counts = dependent_counts(request)
    fixed_result = place_fixed(request, counts)
    if isinstance(fixed_result, frozenset):
        explanations = tuple(
            infeasible_explanation(activity, request, counts[activity.activity_id])
            for activity in request.activities
            if activity.activity_id in fixed_result
        )
        unscheduled = tuple(
            UnscheduledActivity(
                activity_id=activity_id,
                reason=UnscheduledReason.FIXED_CONFLICT,
                detail=(
                    "Fixed or protected reservation violates a hard time constraint "
                    "or overlaps another reservation."
                ),
                blocking_activity_ids=tuple(sorted(fixed_result - {activity_id})),
            )
            for activity_id in sorted(fixed_result)
        )
        return ProposedDay(
            input_fingerprint=fingerprint,
            state_revision=request.state_revision,
            timezone=request.timezone,
            status=PlanStatus.INFEASIBLE,
            schedule_resolution=request.schedule_resolution,
            internal_plan=(),
            calendar_projection=(),
            explanations=explanations,
            unscheduled=unscheduled,
            diff=build_diff(request, ()),
        )
    placements = list(fixed_result)
    unscheduled = place_flexible(request, placements, counts)
    if broken := invalid_dependencies(placements):
        # Fixed reservations cannot silently disappear or be shown with unmet prerequisites.
        rejected = [
            UnscheduledActivity(
                activity_id=activity.activity_id,
                reason=UnscheduledReason.DEPENDENCY_UNSCHEDULED,
                detail=(
                    "Required fixed schedule has unmet or reversed dependencies; no plan emitted."
                ),
                blocking_activity_ids=tuple(sorted(broken)),
            )
            for activity in request.activities
        ]
        return ProposedDay(
            input_fingerprint=fingerprint,
            state_revision=request.state_revision,
            timezone=request.timezone,
            status=PlanStatus.INFEASIBLE,
            schedule_resolution=request.schedule_resolution,
            internal_plan=(),
            calendar_projection=(),
            explanations=tuple(
                explain_activity(activity, request, [], rejected, counts[activity.activity_id])
                for activity in request.activities
            ),
            unscheduled=tuple(rejected),
            diff=build_diff(request, ()),
        )
    blocks = build_internal_plan(request, tuple(placements))
    explanations = tuple(
        explain_activity(
            activity,
            request,
            placements,
            unscheduled,
            counts[activity.activity_id],
        )
        for activity in request.activities
    )
    if unscheduled:
        status = PlanStatus.PARTIAL if placements else PlanStatus.INFEASIBLE
    else:
        status = PlanStatus.FEASIBLE
    return ProposedDay(
        input_fingerprint=fingerprint,
        state_revision=request.state_revision,
        timezone=request.timezone,
        status=status,
        schedule_resolution=request.schedule_resolution,
        internal_plan=blocks,
        calendar_projection=project_calendar(blocks),
        explanations=explanations,
        unscheduled=tuple(unscheduled),
        diff=build_diff(request, blocks),
    )
