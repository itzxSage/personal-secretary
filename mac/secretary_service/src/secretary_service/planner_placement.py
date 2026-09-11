"""Minute-slot placement for fixed, protected, and flexible activities."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from secretary_service.planner_models import (
    ActivityFlexibility,
    DayPlanRequest,
    PlanActivity,
)
from secretary_service.planner_results import (
    ScoreBreakdown,
    UnscheduledActivity,
    UnscheduledReason,
)
from secretary_service.planner_scoring import score_activity


@dataclass(frozen=True, slots=True)
class Placement:
    """Internal reservation for one activity and its travel."""

    activity: PlanActivity
    starts_at: datetime
    ends_at: datetime
    score: ScoreBreakdown

    @property
    def reservation_start(self) -> datetime:
        """Return the start including travel before the activity."""
        return self.starts_at.astimezone(UTC) - timedelta(
            minutes=self.activity.travel_minutes_before
        )

    @property
    def reservation_end(self) -> datetime:
        """Return the end including travel after the activity."""
        return self.ends_at.astimezone(UTC) + timedelta(minutes=self.activity.travel_minutes_after)


def dependent_counts(request: DayPlanRequest) -> dict[str, int]:
    """Count direct dependents for deterministic unblock scoring."""
    return {
        activity.activity_id: sum(
            activity.activity_id in candidate.dependencies for candidate in request.activities
        )
        for activity in request.activities
    }


def place_fixed(
    request: DayPlanRequest,
    counts: dict[str, int],
) -> tuple[Placement, ...] | frozenset[str]:
    """Reserve fixed/protected intervals or return every conflicting ID."""
    placements: list[Placement] = []
    conflicts: set[str] = set()
    fixed = sorted(
        (
            activity
            for activity in request.activities
            if activity.flexibility is not ActivityFlexibility.FLEXIBLE
        ),
        key=lambda activity: (activity.fixed_start or request.window_start, activity.activity_id),
    )
    for activity in fixed:
        starts_at = activity.fixed_start or request.window_start
        ends_at = activity.fixed_end or starts_at
        placement = Placement(
            activity=activity,
            starts_at=starts_at,
            ends_at=ends_at,
            score=score_activity(activity, request, starts_at, counts[activity.activity_id]),
        )
        if placement.reservation_start.astimezone(UTC) < request.window_start.astimezone(
            UTC
        ) or placement.reservation_end.astimezone(UTC) > request.window_end.astimezone(UTC):
            conflicts.add(activity.activity_id)
        if not satisfies_time_bounds(placement, request):
            conflicts.add(activity.activity_id)
        for existing in placements:
            if overlaps(placement, existing):
                conflicts.update((activity.activity_id, existing.activity.activity_id))
        placements.append(placement)
    return frozenset(conflicts) if conflicts else tuple(placements)


def place_flexible(
    request: DayPlanRequest,
    placements: list[Placement],
    counts: dict[str, int],
) -> list[UnscheduledActivity]:
    """Place flexible activities by score, dependency order, then stable ID."""
    flexible = {
        activity.activity_id: activity
        for activity in request.activities
        if activity.flexibility is ActivityFlexibility.FLEXIBLE
    }
    all_ids = {activity.activity_id for activity in request.activities}
    scheduled_ids = {placement.activity.activity_id for placement in placements}
    unscheduled: list[UnscheduledActivity] = []
    while flexible:
        missing = sorted(
            activity_id
            for activity_id, activity in flexible.items()
            if not set(activity.dependencies) <= all_ids
        )
        for activity_id in missing:
            activity = flexible.pop(activity_id)
            unscheduled.append(
                UnscheduledActivity(
                    activity_id=activity_id,
                    reason=UnscheduledReason.MISSING_DEPENDENCY,
                    detail="Dependency does not exist in this canonical snapshot.",
                    blocking_activity_ids=tuple(sorted(set(activity.dependencies) - all_ids)),
                )
            )
        eligible = [
            activity
            for activity in flexible.values()
            if set(activity.dependencies) <= scheduled_ids
        ]
        candidates = [
            placement
            for activity in eligible
            if (placement := best_placement(activity, request, placements, counts)) is not None
        ]
        if candidates:
            chosen = min(
                candidates,
                key=lambda placement: (
                    -placement.score.total,
                    placement.starts_at.astimezone(UTC),
                    placement.activity.activity_id,
                ),
            )
            placements.append(chosen)
            scheduled_ids.add(chosen.activity.activity_id)
            del flexible[chosen.activity.activity_id]
            continue
        if eligible:
            for activity in sorted(eligible, key=lambda item: item.activity_id):
                unscheduled.append(
                    UnscheduledActivity(
                        activity_id=activity.activity_id,
                        reason=UnscheduledReason.NO_CAPACITY,
                        detail="No nonconflicting minute interval satisfies all hard constraints.",
                    )
                )
                del flexible[activity.activity_id]
            continue
        unscheduled.extend(
            UnscheduledActivity(
                activity_id=activity.activity_id,
                reason=UnscheduledReason.DEPENDENCY_UNSCHEDULED,
                detail="A required dependency was not scheduled.",
                blocking_activity_ids=tuple(sorted(set(activity.dependencies) - scheduled_ids)),
            )
            for activity in sorted(flexible.values(), key=lambda item: item.activity_id)
        )
        flexible.clear()
    return unscheduled


def best_placement(
    activity: PlanActivity,
    request: DayPlanRequest,
    placements: list[Placement],
    counts: dict[str, int],
) -> Placement | None:
    """Choose the highest-scoring free minute, breaking ties chronologically."""
    zone = ZoneInfo(request.timezone)
    bounds = placement_bounds(activity, request, placements)
    if bounds is None:
        return None
    lower, upper = bounds
    candidates: list[Placement] = []
    starts_at = lower
    while starts_at <= upper:
        local_start = starts_at.astimezone(zone)
        candidate = Placement(
            activity=activity,
            starts_at=local_start,
            ends_at=(starts_at + timedelta(minutes=activity.duration_minutes)).astimezone(zone),
            score=score_activity(activity, request, local_start, counts[activity.activity_id]),
        )
        if all(not overlaps(candidate, existing) for existing in placements):
            candidates.append(candidate)
        starts_at += timedelta(minutes=1)
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda placement: (-placement.score.total, placement.starts_at.astimezone(UTC)),
    )


def placement_bounds(
    activity: PlanActivity, request: DayPlanRequest, placements: list[Placement]
) -> tuple[datetime, datetime] | None:
    """Intersect UTC start bounds, finish limits, and already placed dependencies."""
    lower = request.window_start.astimezone(UTC) + timedelta(minutes=activity.travel_minutes_before)
    if activity.earliest_start is not None:
        lower = max(lower, activity.earliest_start.astimezone(UTC))
    predecessors = [
        item for item in placements if item.activity.activity_id in activity.dependencies
    ]
    if len({item.activity.activity_id for item in predecessors}) != len(set(activity.dependencies)):
        return None
    for predecessor in predecessors:
        lower = max(lower, predecessor.ends_at.astimezone(UTC))
    upper = request.window_end.astimezone(UTC) - timedelta(
        minutes=activity.duration_minutes + activity.travel_minutes_after
    )
    if activity.latest_end is not None:
        upper = min(
            upper,
            activity.latest_end.astimezone(UTC) - timedelta(minutes=activity.duration_minutes),
        )
    if (deadline := hard_deadline(activity, request)) is not None:
        upper = min(upper, deadline - timedelta(minutes=activity.duration_minutes))
    # Fixed/protected successors are already reserved, but still need their prerequisites first.
    for successor in placements:
        if activity.activity_id in successor.activity.dependencies:
            upper = min(
                upper,
                successor.starts_at.astimezone(UTC) - timedelta(minutes=activity.duration_minutes),
            )
    return lower, upper


def overlaps(left: Placement, right: Placement) -> bool:
    """Return whether two complete activity reservations overlap in real time."""
    return left.reservation_start.astimezone(UTC) < right.reservation_end.astimezone(
        UTC
    ) and right.reservation_start.astimezone(UTC) < left.reservation_end.astimezone(UTC)


def hard_deadline(activity: PlanActivity, request: DayPlanRequest) -> datetime | None:
    """Relax only explicitly selected deadlines already missed before this planning window."""
    if activity.deadline is None:
        return None
    deadline = activity.deadline.astimezone(UTC)
    if activity.recover_missed_deadline and deadline < request.window_start.astimezone(UTC):
        return None
    return deadline


def satisfies_time_bounds(placement: Placement, request: DayPlanRequest) -> bool:
    """Apply the same hard start/finish contract to fixed and flexible activities."""
    activity = placement.activity
    starts_at, ends_at = placement.starts_at.astimezone(UTC), placement.ends_at.astimezone(UTC)
    deadline = hard_deadline(activity, request)
    return (
        (activity.earliest_start is None or starts_at >= activity.earliest_start.astimezone(UTC))
        and (activity.latest_end is None or ends_at <= activity.latest_end.astimezone(UTC))
        and (deadline is None or ends_at <= deadline)
    )


def invalid_dependencies(placements: list[Placement]) -> frozenset[str]:
    """Reject incomplete or reversed dependencies, including fixed events and cycles."""
    by_id = {placement.activity.activity_id: placement for placement in placements}
    return frozenset(
        placement.activity.activity_id
        for placement in placements
        if any(
            dependency not in by_id
            or by_id[dependency].ends_at.astimezone(UTC) > placement.starts_at.astimezone(UTC)
            for dependency in placement.activity.dependencies
        )
    )
