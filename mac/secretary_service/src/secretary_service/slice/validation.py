"""Provider-independent checks before an interpreted plan reaches trusted planning."""

from secretary_service.planner_models import ActivityFlexibility, DayPlanRequest


class InterpretationProviderError(Exception):
    """Redacted provider failure; never includes credentials, prompts or response bodies."""


def preserve_plan_constraints(base: DayPlanRequest, plan: DayPlanRequest) -> None:
    """Keep canonical metadata and protected activities; prevent recovery elevation."""
    metadata = (
        "plan_date",
        "timezone",
        "window_start",
        "window_end",
        "state_revision",
        "expected_state_revision",
        "baseline_blocks",
        "schedule_resolution",
        "energy_windows",
    )
    proposed = {activity.activity_id: activity for activity in plan.activities}
    protected = [
        item for item in base.activities if item.flexibility is ActivityFlexibility.PROTECTED
    ]
    recovery_allowed = {
        item.activity_id for item in base.activities if item.recover_missed_deadline
    }
    if (
        any(getattr(plan, field) != getattr(base, field) for field in metadata)
        or any(proposed.get(item.activity_id) != item for item in protected)
        or any(
            item.recover_missed_deadline and item.activity_id not in recovery_allowed
            for item in plan.activities
        )
    ):
        message = "interpretation changed protected or canonical planning constraints"
        raise InterpretationProviderError(message)
