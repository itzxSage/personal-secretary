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
        return self.starts_at - timedelta(minutes=self.activity.travel_minutes_before)

    @property
    def reservation_end(self) -> datetime:
        """Return the end including travel after the activity."""
        return self.ends_at + timedelta(minutes=self.activity.travel_minutes_after)


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
    lower = request.window_start.astimezone(UTC) + timedelta(minutes=activity.travel_minutes_before)
    if activity.earliest_start is not None:
        lower = max(lower, activity.earliest_start.astimezone(UTC))
    upper = request.window_end.astimezone(UTC) - timedelta(
        minutes=activity.duration_minutes + activity.travel_minutes_after
    )
    if activity.latest_end is not None:
        upper = min(
            upper,
            activity.latest_end.astimezone(UTC) - timedelta(minutes=activity.duration_minutes),
        )
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


def overlaps(left: Placement, right: Placement) -> bool:
    """Return whether two complete activity reservations overlap in real time."""
    return left.reservation_start.astimezone(UTC) < right.reservation_end.astimezone(
        UTC
    ) and right.reservation_start.astimezone(UTC) < left.reservation_end.astimezone(UTC)
