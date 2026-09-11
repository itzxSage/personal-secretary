"""Typed fixtures shared by governed-learning tests."""

from uuid import UUID

from secretary_service.learning.models import (
    ActualOutcome,
    LearningObservation,
    LearningProvenance,
    LearningSource,
    ObservationTrigger,
    PlannedActivity,
)
from secretary_service.models import ActorId, CorrelationId, RecordId, TransitionContext
from tests.helpers import FakeClock

WORK_SCHEDULE_TRIGGER = ObservationTrigger(kind="incoming_message", source="work_dispatch")
WORK_SCHEDULE_STEPS = (
    "extract date",
    "extract warehouse arrival time",
    "infer work block",
    "add commute",
    "recalculate wake time",
    "recalculate bedtime",
    "replan flexible calendar blocks",
)


def record_id(suffix: int) -> RecordId:
    return RecordId(UUID(f"70000000-0000-0000-0000-{suffix:012d}"))


def context(clock: FakeClock, correlation_id: str) -> TransitionContext:
    return TransitionContext(
        actor=ActorId("user"),
        correlation_id=CorrelationId(correlation_id),
        occurred_at=clock.now(),
    )


def observation(clock: FakeClock, suffix: int) -> LearningObservation:
    return LearningObservation(
        observation_id=record_id(suffix),
        planned=PlannedActivity(
            activity_id=record_id(suffix + 1000),
            title="Deep work",
            planned_start=clock.now(),
            planned_end=clock.now(),
        ),
        outcome=ActualOutcome.COMPLETED,
        actual_start=clock.now(),
        actual_end=clock.now(),
        trigger=WORK_SCHEDULE_TRIGGER,
        procedure_steps=WORK_SCHEDULE_STEPS,
        provenance=LearningProvenance(
            source=LearningSource.PLANNER,
            source_id="planner:event:42",
            captured_at=clock.now(),
        ),
        observed_at=clock.now(),
    )


def divergent_observation(clock: FakeClock, suffix: int) -> LearningObservation:
    return observation(clock, suffix).model_copy(
        update={
            "outcome": ActualOutcome.ABANDONED,
            "trigger": None,
            "procedure_steps": (),
            "context_tags": ("a physical shift",),
        }
    )
