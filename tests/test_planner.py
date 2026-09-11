"""Deterministic proposed-day planner tests."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from secretary_service.models import FrozenModel
from secretary_service.planner import StalePlannerStateError, propose_day
from secretary_service.planner_models import (
    ActivityFlexibility,
    DayPlanRequest,
    EnergyWindow,
    PlanActivity,
    ScheduleResolution,
)
from secretary_service.planner_results import PlanStatus, UnscheduledReason
from tests.planner_fixtures import eastern, seeded_request


class ExistingBoundaryFixture(FrozenModel):
    """Minimal fixture proving the established immutable boundary convention."""

    observed_at: datetime


def test_baseline_domain_models_are_frozen() -> None:
    # Given: an existing Life Engine boundary model.
    fixture = ExistingBoundaryFixture(observed_at=datetime(2026, 9, 5, tzinfo=UTC))

    # When/Then: mutation is rejected by the unchanged FrozenModel contract.
    with pytest.raises(ValidationError, match="frozen"):
        fixture.observed_at = datetime(2026, 9, 6, tzinfo=UTC)


def test_seeded_day_has_exact_internal_schedule_projection_and_overdue_rationale() -> None:
    # Given: the seeded work and personal-life fixture.
    request = seeded_request()

    # When: the deterministic planner proposes the day.
    proposal = propose_day(request)

    # Then: every minute is represented by the exact stable internal schedule.
    assert proposal.status is PlanStatus.FEASIBLE
    assert proposal.schedule_resolution == ScheduleResolution()
    assert [
        (block.title, block.starts_at.strftime("%H:%M"), block.ends_at.strftime("%H:%M"))
        for block in proposal.internal_plan
    ] == [
        ("Discussion post", "08:00", "08:45"),
        ("Available", "08:45", "09:00"),
        ("Work", "09:00", "17:00"),
        ("Travel to Farmers", "17:00", "17:15"),
        ("Farmers", "17:15", "17:45"),
        ("Travel from Farmers", "17:45", "18:00"),
        ("Workout", "18:00", "19:00"),
        ("Laundry", "19:00", "19:30"),
        ("LifeOS", "19:30", "20:30"),
        ("Available", "20:30", "22:00"),
    ]
    assert [
        (block.title, block.starts_at.strftime("%H:%M"), block.ends_at.strftime("%H:%M"))
        for block in proposal.calendar_projection
    ] == [
        ("Coursework deadline", "08:00", "08:45"),
        ("Work", "09:00", "17:00"),
        ("Farmers errand", "17:00", "18:00"),
        ("Workout", "18:00", "19:00"),
        ("Evening reset", "19:00", "20:30"),
    ]
    post = next(item for item in proposal.explanations if item.activity_id == "discussion-post")
    assert post.summary == (
        "Scheduled first because its deadline was overdue by 8 hours and 1 minute "
        "at the planning window."
    )
    assert tuple(post.model_dump()) == (
        "activity_id",
        "code",
        "summary",
        "score",
        "scheduled_block_ids",
        "constraints",
    )
    assert post.score.deadline > post.score.goal
    assert tuple(proposal.diff.model_dump()) == ("added", "removed", "moved")
    assert [change.block_id for change in proposal.diff.added] == [
        "activity:discussion-post",
        "activity:farmers",
        "activity:laundry",
        "activity:lifeos",
        "activity:work",
        "activity:workout",
        "travel-after:farmers",
        "travel-before:farmers",
    ]
    assert proposal.diff.removed == ()
    assert proposal.diff.moved == ()


def test_scoring_covers_dependencies_travel_energy_and_goals_with_stable_ties() -> None:
    # Given: equal tasks plus a dependency-unblocking task with travel and energy constraints.
    activities = (
        PlanActivity(
            activity_id="z-tie",
            title="Z tie",
            flexibility=ActivityFlexibility.FLEXIBLE,
            duration_minutes=15,
        ),
        PlanActivity(
            activity_id="a-tie",
            title="A tie",
            flexibility=ActivityFlexibility.FLEXIBLE,
            duration_minutes=15,
        ),
        PlanActivity(
            activity_id="foundation",
            title="Foundation",
            flexibility=ActivityFlexibility.FLEXIBLE,
            duration_minutes=15,
            travel_minutes_before=5,
            energy_required=4,
            goal_weight=3,
        ),
        PlanActivity(
            activity_id="dependent",
            title="Dependent",
            flexibility=ActivityFlexibility.FLEXIBLE,
            duration_minutes=15,
            dependencies=("foundation",),
        ),
    )
    request = seeded_request().model_copy(
        update={
            "activities": activities,
            "window_start": eastern(8),
            "window_end": eastern(10),
            "energy_windows": (EnergyWindow(starts_at=eastern(8), ends_at=eastern(9), level=4),),
        }
    )

    # When: the same request is planned twice.
    first = propose_day(request)
    second = propose_day(request)

    # Then: IDs and ordering are stable and all required score dimensions are explicit.
    assert first == second
    activity_order = [block.activity_id for block in first.internal_plan if block.activity_id]
    assert activity_order.index("foundation") < activity_order.index("dependent")
    assert activity_order.index("a-tie") < activity_order.index("z-tie")
    foundation = next(item for item in first.explanations if item.activity_id == "foundation")
    assert foundation.score.dependency > 0
    assert foundation.score.travel < 0
    assert foundation.score.energy > 0
    assert foundation.score.goal > 0


def test_impossible_plan_returns_typed_infeasibility_without_misleading_blocks() -> None:
    # Given: fixed and protected events occupying the same minutes.
    conflict = PlanActivity(
        activity_id="conflict",
        title="Conflict",
        flexibility=ActivityFlexibility.PROTECTED,
        duration_minutes=60,
        fixed_start=eastern(9),
        fixed_end=eastern(10),
    )
    request = seeded_request().model_copy(
        update={"activities": (seeded_request().activities[0], conflict)}
    )

    # When: the impossible request is planned.
    proposal = propose_day(request)

    # Then: it fails safely rather than emitting overlapping calendar output.
    assert proposal.status is PlanStatus.INFEASIBLE
    assert proposal.internal_plan == ()
    assert proposal.calendar_projection == ()
    assert {item.reason for item in proposal.unscheduled} == {UnscheduledReason.FIXED_CONFLICT}


def test_duplicate_and_malformed_inputs_are_rejected_at_boundary() -> None:
    # Given: duplicate IDs and a naive planning timestamp.
    activity = seeded_request().activities[0]

    # When/Then: malformed requests never enter planning logic.
    duplicate = seeded_request().model_dump()
    duplicate["activities"] = [activity.model_dump(), activity.model_dump()]
    with pytest.raises(ValidationError, match="duplicate activity_id"):
        _ = DayPlanRequest.model_validate(duplicate)
    with pytest.raises(ValidationError, match="timezone-aware"):
        _ = DayPlanRequest(
            plan_date=date(2026, 9, 7),
            timezone="America/New_York",
            window_start=eastern(8).replace(tzinfo=None),
            window_end=eastern(22),
            state_revision=1,
            expected_state_revision=1,
            activities=(),
        )


def test_nonexistent_dst_time_is_rejected_and_fall_back_fold_is_deterministic() -> None:
    # Given: a nonexistent spring-forward timestamp and an explicit fall-back fold.
    zone = ZoneInfo("America/New_York")
    nonexistent = datetime(2026, 3, 8, 2, 30, tzinfo=zone)

    # When/Then: nonexistent wall time fails, while an explicit repeated hour is stable.
    with pytest.raises(ValidationError, match="nonexistent local time"):
        _ = DayPlanRequest(
            plan_date=date(2026, 3, 8),
            timezone="America/New_York",
            window_start=nonexistent,
            window_end=datetime(2026, 3, 8, 4, tzinfo=zone),
            state_revision=1,
            expected_state_revision=1,
            activities=(),
        )
    repeated = datetime(2026, 11, 1, 1, 0, tzinfo=zone, fold=1)
    request = DayPlanRequest(
        plan_date=date(2026, 11, 1),
        timezone="America/New_York",
        window_start=repeated,
        window_end=datetime(2026, 11, 1, 2, 0, tzinfo=zone),
        state_revision=1,
        expected_state_revision=1,
        activities=(),
    )
    proposal = propose_day(request)
    assert proposal.internal_plan[0].duration_minutes == 60


def test_micro_events_never_render_and_grouping_preserves_actionable_context() -> None:
    # Given: adjacent grouped work around a personal micro-event.
    micro = PlanActivity(
        activity_id="medication",
        title="Medication",
        flexibility=ActivityFlexibility.FIXED,
        duration_minutes=5,
        fixed_start=eastern(20, 30),
        fixed_end=eastern(20, 35),
        personal_micro_event=True,
    )
    request = seeded_request().model_copy(
        update={"activities": (*seeded_request().activities, micro)}
    )

    # When: the day is projected for Calendar.
    proposal = propose_day(request)

    # Then: no personal micro-event leaks and grouped detail remains actionable.
    assert all("Medication" not in block.title for block in proposal.calendar_projection)
    evening = next(
        block for block in proposal.calendar_projection if block.title == "Evening reset"
    )
    assert evening.source_activity_ids == ("laundry", "lifeos")
    assert evening.segment_titles == ("Laundry", "LifeOS")
    assert evening.guidance == (
        "Start and fold laundry",
        "Finish the planner implementation",
    )


def test_stale_state_is_rejected_before_output_is_generated() -> None:
    # Given: a proposal request based on an old canonical revision.
    request = seeded_request().model_copy(update={"expected_state_revision": 6})

    # When/Then: stale state cannot produce a plausible but outdated proposal.
    with pytest.raises(StalePlannerStateError, match="expected revision 6, current revision 7"):
        _ = propose_day(request)
