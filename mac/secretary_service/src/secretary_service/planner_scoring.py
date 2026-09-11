"""Deterministic priority and placement scoring."""

from datetime import UTC, datetime, timedelta

from secretary_service.planner_models import DayPlanRequest, PlanActivity
from secretary_service.planner_results import ScoreBreakdown


def score_activity(
    activity: PlanActivity,
    request: DayPlanRequest,
    starts_at: datetime,
    dependent_count: int,
) -> ScoreBreakdown:
    """Score an activity at a candidate start with explicit components."""
    deadline_score = 0
    if activity.deadline is not None:
        deadline_delta = int(
            (
                activity.deadline.astimezone(UTC) - request.window_start.astimezone(UTC)
            ).total_seconds()
            // 60
        )
        if deadline_delta < 0:
            deadline_score = 10_000 + min(-deadline_delta, 30 * 24 * 60)
        elif activity.deadline.astimezone(UTC) <= request.window_end.astimezone(UTC):
            deadline_score = 5_000 - deadline_delta
        else:
            deadline_score = max(0, 1_000 - deadline_delta // 60)
    dependency_score = dependent_count * 400
    travel_score = -5 * (activity.travel_minutes_before + activity.travel_minutes_after)
    activity_end = starts_at.astimezone(UTC) + timedelta(minutes=activity.duration_minutes)
    matching_levels = [
        window.level
        for window in request.energy_windows
        if window.starts_at.astimezone(UTC) <= starts_at.astimezone(UTC)
        and activity_end <= window.ends_at.astimezone(UTC)
    ]
    energy_score = 0
    if matching_levels:
        best_level = min(matching_levels, key=lambda level: abs(level - activity.energy_required))
        energy_score = max(0, 300 - 100 * abs(best_level - activity.energy_required))
    goal_score = activity.goal_weight * 100
    importance_score = activity.importance * 100
    total = (
        deadline_score
        + dependency_score
        + travel_score
        + energy_score
        + goal_score
        + importance_score
    )
    return ScoreBreakdown(
        deadline=deadline_score,
        dependency=dependency_score,
        travel=travel_score,
        energy=energy_score,
        goal=goal_score,
        importance=importance_score,
        total=total,
    )
