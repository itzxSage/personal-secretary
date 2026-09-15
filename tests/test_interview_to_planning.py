"""Integration test: real interview → planning_knowledge → week-plan preview.

Traces the REAL interview→planner path end to end. A LifeInterview with the
week_planning objective is driven through 5+ WEEK_PLANNING_TOPICS with
realistic free-text answers, the confirmation turn is exercised for every
ROUTINE topic, and the resulting planning knowledge is fed into
WeekPlanningService.preview() to produce a real proposal with scheduled blocks.

No RoutineDetails are seeded directly and the planner is never mocked.
"""

from datetime import time, timedelta

from secretary_service.enrollment import DeviceRegistry
from secretary_service.life_interview import InterviewReply, LifeInterview
from secretary_service.life_knowledge import KnowledgeKind
from secretary_service.life_model import LifeModel
from secretary_service.planner_results import PlanStatus
from secretary_service.storage import EncryptedStateStore
from secretary_service.week_planning import WeekPlanningService
from tests.goals_memory_helpers import context
from tests.helpers import FakeClock
from tests.test_google_calendar import make_adapter
from tests.test_life_interview import answer, confirm_routine, skip_to


def run_week_planning_interview(
    store: EncryptedStateStore, clock: FakeClock
) -> InterviewReply:
    """Drive the real interview through 5+ week-planning topics."""
    engine = LifeInterview(store.memory, "user")
    reply = engine.begin(context(clock, "begin"), objective="week_planning")
    assert reply.question is not None
    assert reply.question.key == "work.role"
    # FACT topic: no confirmation turn, no planning scope.
    reply = answer(
        engine, reply, "I'm a staff engineer at Acme, working remotely from Toronto.", clock
    )
    assert reply.question is not None
    assert reply.question.key == "work.schedule"
    # FACT topic: planning_allowed=True, stays a gap (no RoutineDetails).
    reply = answer(engine, reply, "I work 9am to 5pm on weekdays, in America/Toronto.", clock)
    assert reply.question is not None
    assert reply.question.key == "planning.fixed_commitments"
    # COMMITMENT topic: planning_allowed=True, stays a gap (no RoutineDetails).
    reply = answer(
        engine,
        reply,
        "Tuesday 7pm choir rehearsal at the community center, and Thursday 6pm therapy.",
        clock,
    )
    # VALUES is a sensitive domain: grant the optional permission first.
    assert reply.question is not None
    assert reply.question.key == "permission.values"
    assert reply.question.mode == "permission"
    reply = answer(engine, reply, "yes", clock)
    assert reply.question is not None
    assert reply.question.key == "values.commitments"
    reply = answer(engine, reply, "I volunteer at the food bank on Saturday mornings.", clock)
    # Skip the education topics to reach the first ROUTINE topic.
    reply = skip_to(engine, reply, "routines.sleep", clock)
    # ROUTINE topics: the confirmation turn appears and is confirmed.
    reply = confirm_routine(
        engine, reply, "I sleep from 10pm to 6am daily, about 8 hours.", clock
    )
    reply = confirm_routine(
        engine,
        reply,
        "I run on Monday, Wednesday, and Friday mornings at 7am for 45 minutes.",
        clock,
    )
    return confirm_routine(engine, reply, "I eat lunch at 12:30pm daily for 30 minutes.", clock)


def test_interview_to_planning_preview_traces_real_path(
    store: EncryptedStateStore, clock: FakeClock
) -> None:
    """The real interview produces planning knowledge that preview schedules."""
    run_week_planning_interview(store, clock)

    # The confirmed routines plus the two planning-allowed gaps are the only
    # planning knowledge: work.role and values.commitments never gain a
    # PLANNING scope, so they are filtered out.
    planning = LifeModel(store.memory).planning_knowledge("user", clock.now())
    assert len(planning) == 5
    by_key = {view.record.knowledge.key: view for view in planning}

    sleep = by_key["routines.sleep"]
    assert sleep.record.knowledge.kind is KnowledgeKind.ROUTINE
    assert sleep.record.knowledge.planning_allowed
    routine = sleep.record.knowledge.routine
    assert routine is not None
    assert routine.days == frozenset(range(7))
    assert routine.start_time == time(22, 0)
    assert routine.duration_minutes == 480

    exercise = by_key["routines.exercise"]
    assert exercise.record.knowledge.kind is KnowledgeKind.ROUTINE
    assert exercise.record.knowledge.planning_allowed
    routine = exercise.record.knowledge.routine
    assert routine is not None
    assert routine.days == frozenset({0, 2, 4})
    assert routine.start_time == time(7, 0)
    assert routine.duration_minutes == 45

    meals = by_key["routines.meals"]
    assert meals.record.knowledge.kind is KnowledgeKind.ROUTINE
    assert meals.record.knowledge.planning_allowed
    routine = meals.record.knowledge.routine
    assert routine is not None
    assert routine.days == frozenset(range(7))
    assert routine.start_time == time(12, 30)
    assert routine.duration_minutes == 30

    # FACT/COMMITMENT topics are planning-allowed but carry no RoutineDetails.
    for key in ("work.schedule", "planning.fixed_commitments"):
        view = by_key[key]
        assert view.record.knowledge.planning_allowed
        assert view.record.knowledge.routine is None
        assert view.record.knowledge.kind in {KnowledgeKind.FACT, KnowledgeKind.COMMITMENT}

    # Feed the real planning knowledge into the governed preview path.
    adapter, _ = make_adapter(clock)
    devices = DeviceRegistry(clock, store.devices)
    planner = WeekPlanningService(
        clock, devices, store, adapter, b"week-planning-lease-key", timedelta(minutes=10)
    )
    result = planner.preview("user", clock.now(), "UTC")

    # A feasible-or-partial proposal with actual scheduled blocks, not just gap
    # fill. The last-day sleep occurrence (22:00-06:00) crosses the 7-day window
    # boundary, so the planner legitimately reports PARTIAL for that one
    # occurrence; every other routine occurrence is scheduled.
    assert result.status in {PlanStatus.FEASIBLE, PlanStatus.PARTIAL}
    assert result.blocks
    scheduled = [block for block in result.blocks if block.activity_id is not None]
    assert scheduled
    assert all(block.activity_id.startswith("routine:") for block in scheduled)
    # The FACT/COMMITMENT topics remain explicit gaps, never silently dropped.
    assert {gap.memory_id for gap in result.gaps} == {
        by_key["work.schedule"].record.memory_id,
        by_key["planning.fixed_commitments"].record.memory_id,
    }
