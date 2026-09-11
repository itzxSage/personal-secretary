"""Regression and generated-case invariants for hard scheduling constraints."""

from datetime import UTC, date, datetime, timedelta
from itertools import pairwise, permutations
from zoneinfo import ZoneInfo

import pytest

from secretary_service.planner import propose_day
from secretary_service.planner_models import (
    ActivityFlexibility,
    DayPlanRequest,
    EnergyWindow,
    PlanActivity,
)
from secretary_service.planner_results import PlanBlockKind, PlanStatus
from tests.planner_fixtures import eastern, seeded_request


def task(name: str, **constraints: object) -> PlanActivity:
    return PlanActivity.model_validate(
        {"activity_id": name, "title": name, "flexibility": "flexible", "duration_minutes": 30}
        | constraints
    )


def request_for(*activities: PlanActivity) -> DayPlanRequest:
    return seeded_request().model_copy(
        update={
            "activities": activities,
            "window_end": eastern(16),
            "energy_windows": (EnergyWindow(starts_at=eastern(14), ends_at=eastern(16), level=5),),
        }
    )


def test_dependent_cannot_fill_morning_gap_before_afternoon_prerequisite() -> None:
    first = task("prepare", earliest_start=eastern(14))
    second = task("present", dependencies=("prepare",))
    plan = propose_day(request_for(second, first))
    blocks = {b.activity_id: b for b in plan.internal_plan if b.kind is PlanBlockKind.FLEXIBLE}
    assert plan.status is PlanStatus.FEASIBLE
    assert blocks["present"].starts_at >= blocks["prepare"].ends_at
    assert blocks["prepare"].starts_at == eastern(14)


@pytest.mark.parametrize("finish", [eastern(8, 29), eastern(8, 30), eastern(9)])
def test_deadline_is_hard_even_when_energy_favors_later(finish: datetime) -> None:
    plan = propose_day(request_for(task("due", deadline=finish, energy_required=5)))
    blocks = [b for b in plan.internal_plan if b.kind is PlanBlockKind.FLEXIBLE]
    if finish == eastern(8, 29):
        assert plan.status is PlanStatus.INFEASIBLE
        assert not blocks
    else:
        assert plan.status is PlanStatus.FEASIBLE
        assert blocks[0].ends_at <= finish


@pytest.mark.parametrize("recover", [False, True])
def test_missed_deadline_requires_explicit_recovery(*, recover: bool) -> None:
    activity = task("overdue", deadline=eastern(7), recover_missed_deadline=recover)
    plan = propose_day(request_for(activity))
    assert plan.status is (PlanStatus.FEASIBLE if recover else PlanStatus.INFEASIBLE)
    if recover:
        assert "explicitly requested recovery" in plan.explanations[0].summary
        assert "recover_missed_deadline=true" in plan.explanations[0].constraints


def test_recovery_never_relaxes_future_deadline_or_latest_end() -> None:
    for deadline, latest_end in [(eastern(8, 20), None), (eastern(7), eastern(8, 20))]:
        activity = task(
            "bounded", deadline=deadline, latest_end=latest_end, recover_missed_deadline=True
        )
        assert propose_day(request_for(activity)).status is PlanStatus.INFEASIBLE


@pytest.mark.parametrize("flexibility", [ActivityFlexibility.FIXED, ActivityFlexibility.PROTECTED])
@pytest.mark.parametrize("constraint", ["deadline", "latest_end", "earliest_start"])
def test_fixed_events_obey_time_bounds(flexibility: ActivityFlexibility, constraint: str) -> None:
    bound = eastern(11) if constraint == "earliest_start" else eastern(10, 15)
    activity = task(
        "appointment",
        flexibility=flexibility,
        fixed_start=eastern(10),
        fixed_end=eastern(10, 30),
        **{constraint: bound},
    )
    plan = propose_day(request_for(activity))
    assert plan.status is PlanStatus.INFEASIBLE
    assert plan.internal_plan == plan.calendar_projection == ()


def test_fixed_successor_constrains_flexible_preparation() -> None:
    appointment = task(
        "appointment",
        flexibility="fixed",
        fixed_start=eastern(10),
        fixed_end=eastern(10, 30),
        dependencies=("prepare",),
    )
    plan = propose_day(request_for(appointment, task("prepare", energy_required=5)))
    assert plan.status is PlanStatus.FEASIBLE
    prepare = next(b for b in plan.internal_plan if b.activity_id == "prepare")
    assert prepare.ends_at <= eastern(10)


@pytest.mark.parametrize("dependency", ["missing", "late", "appointment"])
def test_impossible_fixed_prerequisites_never_produce_a_plausible_schedule(dependency: str) -> None:
    appointment = task(
        "appointment",
        flexibility="fixed",
        fixed_start=eastern(10),
        fixed_end=eastern(10, 30),
        dependencies=("late" if dependency == "appointment" else dependency,),
    )
    late = task(
        "late",
        earliest_start=eastern(14),
        dependencies=("appointment",) if dependency == "appointment" else (),
    )
    plan = propose_day(request_for(appointment, late))
    assert plan.status is PlanStatus.INFEASIBLE
    assert plan.internal_plan == plan.calendar_projection == ()


def test_generated_dependency_chains_obey_time_capacity_and_input_permutation_invariants() -> None:
    for duration in [1, 7, 30, 61]:
        for earliest in [eastern(8), eastern(14), eastern(15)]:
            activities = (
                task(
                    "a", duration_minutes=duration, earliest_start=earliest, travel_minutes_after=7
                ),
                task("b", duration_minutes=duration, dependencies=("a",), deadline=eastern(15, 30)),
                task("c", duration_minutes=duration, dependencies=("a", "b")),
            )
            baseline = propose_day(request_for(*activities))
            for ordering in permutations(activities):
                plan = propose_day(request_for(*ordering))
                assert plan.internal_plan == baseline.internal_plan
                assert plan.status is baseline.status
                occupied = [b for b in plan.internal_plan if b.kind is not PlanBlockKind.AVAILABLE]
                for left, right in pairwise(occupied):
                    assert left.ends_at.astimezone(UTC) <= right.starts_at.astimezone(UTC)
                blocks = {b.activity_id: b for b in occupied if b.kind is PlanBlockKind.FLEXIBLE}
                for activity in activities:
                    if activity.activity_id not in blocks:
                        continue
                    block = blocks[activity.activity_id]
                    assert block.duration_minutes == duration
                    for dependency in activity.dependencies:
                        assert dependency in blocks
                        assert blocks[dependency].ends_at <= block.starts_at
                    if activity.deadline is not None:
                        assert block.ends_at <= activity.deadline


def test_travel_through_fall_back_uses_elapsed_minutes() -> None:
    zone = ZoneInfo("America/New_York")
    start = datetime(2026, 11, 1, 1, 15, tzinfo=zone, fold=1)
    activity = task(
        "appointment",
        flexibility="fixed",
        fixed_start=start,
        fixed_end=datetime(2026, 11, 1, 1, 45, tzinfo=zone, fold=1),
        travel_minutes_before=30,
    )
    request = DayPlanRequest(
        plan_date=date(2026, 11, 1),
        timezone="America/New_York",
        window_start=datetime(2026, 11, 1, 0, tzinfo=zone),
        window_end=datetime(2026, 11, 1, 3, tzinfo=zone),
        state_revision=1,
        expected_state_revision=1,
        activities=(activity,),
    )
    plan = propose_day(request)
    travel = next(b for b in plan.internal_plan if b.kind is PlanBlockKind.TRAVEL)
    assert plan.status is PlanStatus.FEASIBLE
    assert travel.duration_minutes == 30
    assert travel.starts_at.astimezone(UTC) == start.astimezone(UTC) - timedelta(minutes=30)
