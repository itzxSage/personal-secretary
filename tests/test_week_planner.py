"""Whole-week coverage and cross-day hard-constraint regressions."""

from datetime import UTC, datetime, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from secretary_service.planner import propose_week
from secretary_service.planner_models import ActivityFlexibility, PlanActivity, WeekPlanRequest
from secretary_service.planner_results import PlanBlockKind, PlanStatus


def week(start: datetime, *activities: PlanActivity) -> WeekPlanRequest:
    return WeekPlanRequest(
        plan_date=start.date(),
        timezone="America/Chicago",
        window_start=start,
        window_end=(start.astimezone(UTC) + timedelta(days=7)).astimezone(start.tzinfo),
        state_revision=1,
        expected_state_revision=1,
        activities=activities,
    )


@pytest.mark.parametrize(("month", "day"), [(9, 13), (3, 7), (10, 31)])
def test_week_accounts_for_every_elapsed_minute_including_dst(month: int, day: int) -> None:
    start = datetime(2026, month, day, tzinfo=ZoneInfo("America/Chicago"))
    request = week(start)
    plan = propose_week(request)
    assert plan.status is PlanStatus.FEASIBLE
    assert sum(block.duration_minutes for block in plan.internal_plan) == 10_080
    assert plan.internal_plan[0].starts_at == request.window_start
    assert plan.internal_plan[-1].ends_at == request.window_end
    assert all(block.kind is PlanBlockKind.AVAILABLE for block in plan.internal_plan)
    assert not plan.calendar_projection
    assert propose_week(request) == plan


def test_week_preserves_fixed_sleep_and_cross_day_dependency_with_hard_deadline() -> None:
    start = datetime(2026, 9, 13, tzinfo=ZoneInfo("America/Chicago"))
    activities = tuple(
        PlanActivity(
            activity_id=f"sleep-{day}",
            title="Sleep",
            flexibility=ActivityFlexibility.PROTECTED,
            duration_minutes=480,
            fixed_start=start + timedelta(days=day),
            fixed_end=start + timedelta(days=day, hours=8),
        )
        for day in range(7)
    )
    fixed = PlanActivity(
        activity_id="external-appointment",
        title="Existing appointment",
        flexibility=ActivityFlexibility.FIXED,
        duration_minutes=60,
        fixed_start=start + timedelta(days=1, hours=9),
        fixed_end=start + timedelta(days=1, hours=10),
        calendar_eligible=False,
    )
    first = PlanActivity(
        activity_id="prepare",
        title="Prepare",
        flexibility=ActivityFlexibility.FLEXIBLE,
        duration_minutes=60,
        earliest_start=start + timedelta(days=2, hours=23),
        deadline=start + timedelta(days=3),
    )
    second = PlanActivity(
        activity_id="deliver",
        title="Goal work",
        flexibility=ActivityFlexibility.FLEXIBLE,
        duration_minutes=60,
        dependencies=("prepare",),
        deadline=start + timedelta(days=3, hours=9),
        goal_weight=10,
    )
    request = week(start, *activities, fixed, second, first)
    plan = propose_week(request)
    assert plan.status is PlanStatus.FEASIBLE
    assert sum(block.duration_minutes for block in plan.internal_plan) == 10_080
    assert all(left.ends_at == right.starts_at for left, right in pairwise(plan.internal_plan))
    blocks = {block.activity_id: block for block in plan.internal_plan}
    assert blocks["deliver"].starts_at >= blocks["prepare"].ends_at
    assert blocks["deliver"].ends_at == second.deadline
    assert blocks["external-appointment"].starts_at == fixed.fixed_start
    assert all(
        "external-appointment" not in b.source_activity_ids for b in plan.calendar_projection
    )
    assert len(plan.calendar_projection) == 9
    assert len([b for b in plan.internal_plan if b.kind is PlanBlockKind.PROTECTED]) == 7
    assert next(e for e in plan.explanations if e.activity_id == "deliver").score.goal > 0
    assert propose_week(request) == plan


def test_week_rejects_shortened_horizon_and_revalidates_unchecked_copies() -> None:
    start = datetime(2026, 9, 13, tzinfo=ZoneInfo("America/Chicago"))
    request = week(start)
    with pytest.raises(ValidationError, match="10,080"):
        _ = propose_week(request.model_copy(update={"window_end": start + timedelta(days=1)}))
