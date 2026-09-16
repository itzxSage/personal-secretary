"""Persistent, evidence-aware interview behavior shared by voice and text."""

from datetime import time, timedelta

import pytest

from secretary_service.interview_catalog import TOPICS
from secretary_service.life_interview import InterviewReply, LifeInterview
from secretary_service.life_knowledge import (
    KnowledgeKind,
    KnowledgeState,
    LifeDomain,
    RoutineDraft,
    Sensitivity,
)
from secretary_service.life_model import LifeModel
from secretary_service.memory import (
    MemoryProvenance,
    MemoryRecord,
    MemorySource,
    RetrievalScope,
    StaleMemoryRevisionError,
)
from secretary_service.models import ActorId, CorrelationId, TransitionContext
from secretary_service.storage import EncryptedStateStore
from tests.goals_memory_helpers import context
from tests.helpers import FakeClock
from tests.test_life_knowledge import assertion


def test_catalog_covers_every_domain_without_duplicate_keys() -> None:
    assert {topic.domain for topic in TOPICS} == set(LifeDomain)
    assert len({topic.key for topic in TOPICS}) == len(TOPICS)


def answer(
    engine: LifeInterview, reply: InterviewReply, text: str, clock: FakeClock
) -> InterviewReply:
    assert reply.question is not None
    return engine.advance(
        reply.revision,
        "answer",
        text,
        context(clock, f"answer-{reply.revision}"),
        question_key=reply.question.key,
    )


def skip_to(
    engine: LifeInterview, reply: InterviewReply, key: str, clock: FakeClock
) -> InterviewReply:
    while reply.question is not None and reply.question.key != key:
        reply = engine.advance(
            reply.revision, "skip", "", context(clock, "skip"), question_key=reply.question.key
        )
    return reply


def confirm_routine(
    engine: LifeInterview, reply: InterviewReply, text: str, clock: FakeClock
) -> InterviewReply:
    reply = answer(engine, reply, text, clock)
    assert reply.question is not None
    assert reply.question.mode == "confirm"
    return answer(engine, reply, "yes", clock)


def test_answer_saves_immediately_and_resume_does_not_repeat_it(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    engine = LifeInterview(store.memory, "user")
    reply = engine.begin(context(clock, "begin"))
    assert reply.question is not None
    assert reply.question.key == "identity.name"
    reply = answer(engine, reply, "Call me Jared", clock)
    facts = LifeModel(store.memory).knowledge("user", RetrievalScope.PRIVATE, clock.now())
    assert len(facts) == 1
    assert facts[0].record.content == "Call me Jared"
    assert facts[0].state is KnowledgeState.CONFIRMED
    assert facts[0].record.provenance.source_id == "interview:answer-1"
    paused = engine.advance(reply.revision, "pause", "", context(clock, "pause"))
    assert paused.question is None
    recreated = LifeInterview(store.memory, "user")
    assert recreated.begin(context(clock, "reopen")).phase == "paused"
    resumed = recreated.advance(paused.revision, "resume", "", context(clock, "resume"))
    assert resumed.question is not None
    assert resumed.question.key == "identity.base"
    reply = skip_to(engine, resumed, "routines.sleep", clock)
    reply = confirm_routine(engine, reply, "I sleep 10pm-6am daily", clock)
    assert LifeModel(store.memory).planning_knowledge("user", clock.now())


def test_stale_answers_and_wrong_question_are_rejected_without_new_facts(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    engine = LifeInterview(store.memory, "user")
    reply = engine.begin(context(clock, "begin"))
    with pytest.raises(ValueError, match="question changed"):
        _ = engine.advance(
            reply.revision, "answer", "wrong", context(clock, "wrong"), question_key="work.role"
        )
    _ = answer(engine, reply, "Jared", clock)
    with pytest.raises(StaleMemoryRevisionError):
        _ = answer(engine, reply, "Replay", clock)
    facts = LifeModel(store.memory).knowledge("user", RetrievalScope.PRIVATE, clock.now())
    assert len(facts) == 1
    assert facts[0].record.content == "Jared"


def test_imported_or_stale_information_is_reviewed_instead_of_assumed(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    base = assertion(clock)
    assert base.knowledge is not None
    imported = base.model_copy(
        update={
            "content": "JR",
            "knowledge": base.knowledge.model_copy(
                update={
                    "key": "identity.name",
                    "state": KnowledgeState.OBSERVED,
                    "last_confirmed_at": None,
                    "observed_at": clock.now() - timedelta(days=90),
                }
            ),
            "provenance": MemoryProvenance(
                source=MemorySource.IMPORT, source_id="import:1", captured_at=clock.now()
            ),
        }
    )
    store.memory.remember(imported, context(clock, "import"))
    engine = LifeInterview(store.memory, "user")
    reply = engine.begin(context(clock, "begin"))
    assert reply.question is not None
    assert reply.question.mode == "review"
    assert "JR" in reply.question.prompt
    assert "outdated" in reply.question.prompt
    _ = answer(engine, reply, "Jared", clock)
    facts = LifeModel(store.memory).knowledge("user", RetrievalScope.PRIVATE, clock.now())
    assert len(facts) == 1
    assert facts[0].record.content == "Jared"
    assert facts[0].state is KnowledgeState.CONFIRMED


def test_sensitive_topics_are_opt_in_and_sweep_keeps_inboxing_until_done(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    engine = LifeInterview(store.memory, "user")
    reply = engine.begin(context(clock, "begin"))
    while reply.question is not None and reply.question.key != "permission.finances":
        reply = engine.advance(
            reply.revision, "skip", "", context(clock, "skip"), question_key=reply.question.key
        )
    assert reply.question is not None
    reply = answer(engine, reply, "skip", clock)
    assert next(p for p in reply.progress if p.domain is LifeDomain.FINANCES).skipped
    while reply.question is not None and reply.question.key != "open_loops.else":
        if reply.question.key == "routines.sleep":
            reply = answer(engine, reply, "I sleep 10pm-6am daily", clock)
            assert reply.question is not None
            assert reply.question.mode == "confirm"
            reply = answer(engine, reply, "yes", clock)
        else:
            reply = engine.advance(
                reply.revision, "skip", "", context(clock, "skip"), question_key=reply.question.key
            )
    reply = answer(engine, reply, "Return the broken lamp", clock)
    assert reply.question is not None
    assert reply.question.key == "open_loops.else"
    reply = answer(engine, reply, "Call the mechanic", clock)
    assert reply.question is not None
    assert reply.question.key == "open_loops.else"
    reply = answer(engine, reply, "the sweep is complete", clock)
    assert reply.question is not None
    assert reply.question.domain is LifeDomain.NOW
    facts = LifeModel(store.memory).knowledge("user", RetrievalScope.PRIVATE, clock.now())
    assert len(facts) == 3
    open_loops = [
        v
        for v in facts
        if v.record.knowledge is not None and v.record.knowledge.open_loop is not None
    ]
    assert len(open_loops) == 2
    assert all(
        v.record.knowledge is not None
        and v.record.knowledge.open_loop is not None
        and v.record.knowledge.open_loop.disposition == "inbox"
        for v in open_loops
    )
    assert LifeModel(store.memory).planning_knowledge("user", clock.now())


@pytest.mark.parametrize("sensitivity", [Sensitivity.SENSITIVE, Sensitivity.RESTRICTED])
def test_review_of_private_nonimported_history_preserves_classification(
    store: EncryptedStateStore, clock: FakeClock, sensitivity: Sensitivity
) -> None:
    base = assertion(clock)
    assert base.knowledge is not None
    old = base.model_copy(
        update={
            "retrieval_scopes": frozenset({RetrievalScope.PRIVATE}),
            "knowledge": base.knowledge.model_copy(
                update={
                    "key": "identity.name",
                    "state": KnowledgeState.STALE,
                    "sensitivity": sensitivity,
                }
            ),
        }
    )
    store.memory.remember(old, context(clock, "private-history"))
    engine = LifeInterview(store.memory, "user")
    reply = engine.begin(context(clock, "begin"))
    assert reply.question is not None
    assert reply.question.mode == "permission"
    reply = answer(engine, reply, "yes", clock)
    reply = answer(engine, reply, "Current private name", clock)
    facts = LifeModel(store.memory).knowledge("user", RetrievalScope.PRIVATE, clock.now())
    assert len(facts) == 1
    assert facts[0].record.knowledge is not None
    assert facts[0].record.knowledge.sensitivity is sensitivity
    assert facts[0].record.retrieval_scopes == frozenset({RetrievalScope.PRIVATE})
    reply = skip_to(engine, reply, "routines.sleep", clock)
    reply = confirm_routine(engine, reply, "I sleep 10pm-6am daily", clock)
    assert LifeModel(store.memory).planning_knowledge("user", clock.now())


def test_review_changes_only_the_evidence_shown_to_the_user(
    store: EncryptedStateStore, clock: FakeClock
) -> None:
    originals: list[MemoryRecord] = []
    for index in range(1, 5):
        base = assertion(clock, index)
        assert base.knowledge is not None
        old = base.model_copy(
            update={
                "content": f"Historical name {index}",
                "knowledge": base.knowledge.model_copy(
                    update={"key": "identity.name", "state": KnowledgeState.STALE}
                ),
            }
        )
        store.memory.remember(old, context(clock, f"history-{index}"))
        originals.append(old)
    engine = LifeInterview(store.memory, "user")
    reply = engine.begin(context(clock, "begin"))
    assert reply.question is not None
    assert len(reply.question.evidence_ids) == 3
    hidden = next(old for old in originals if old.memory_id not in reply.question.evidence_ids)
    assert hidden.content not in reply.question.prompt
    _ = answer(engine, reply, "Current name", clock)
    records = store.memory.retrieve(RetrievalScope.PRIVATE)
    assert hidden in records


def test_confirmed_routine_becomes_planning_knowledge(
    store: EncryptedStateStore, clock: FakeClock
) -> None:
    engine = LifeInterview(store.memory, "user")
    reply = engine.begin(context(clock, "begin"))
    reply = skip_to(engine, reply, "routines.sleep", clock)
    reply = confirm_routine(engine, reply, "I sleep 10pm-6am daily", clock)
    planning = LifeModel(store.memory).planning_knowledge("user", clock.now())
    assert len(planning) == 1
    view = planning[0]
    assert view.record.knowledge is not None
    assert view.record.knowledge.key == "routines.sleep"
    assert view.record.knowledge.planning_allowed
    assert view.record.confidence == 1.0
    assert RetrievalScope.PLANNING in view.record.retrieval_scopes
    routine = view.record.knowledge.routine
    assert routine is not None
    assert routine.days == frozenset(range(7))
    assert routine.start_time == time(22, 0)
    assert routine.duration_minutes == 480


def test_work_schedule_becomes_planning_gap(store: EncryptedStateStore, clock: FakeClock) -> None:
    engine = LifeInterview(store.memory, "user")
    reply = engine.begin(context(clock, "begin"))
    reply = skip_to(engine, reply, "work.schedule", clock)
    reply = answer(engine, reply, "I work 9-5 weekdays", clock)
    planning = LifeModel(store.memory).planning_knowledge("user", clock.now())
    assert len(planning) == 1
    view = planning[0]
    assert view.record.knowledge is not None
    assert view.record.knowledge.key == "work.schedule"
    assert view.record.knowledge.planning_allowed
    assert view.record.knowledge.kind is KnowledgeKind.FACT
    assert view.record.knowledge.routine is None


def test_unconfirmed_routine_is_not_planning_knowledge(
    store: EncryptedStateStore, clock: FakeClock
) -> None:
    engine = LifeInterview(store.memory, "user")
    reply = engine.begin(context(clock, "begin"))
    reply = skip_to(engine, reply, "routines.sleep", clock)
    reply = answer(engine, reply, "I sleep 10pm-6am daily", clock)
    assert reply.question is not None
    assert reply.question.mode == "confirm"
    assert not LifeModel(store.memory).planning_knowledge("user", clock.now())
    facts = LifeModel(store.memory).knowledge("user", RetrievalScope.PRIVATE, clock.now())
    sleep = next(
        f
        for f in facts
        if f.record.knowledge is not None and f.record.knowledge.key == "routines.sleep"
    )
    assert sleep.record.knowledge is not None
    assert not sleep.record.knowledge.planning_allowed
    assert sleep.record.knowledge.pending_confirmation is not None
    assert sleep.record.confidence == 0.5


def test_skipping_confirmation_keeps_routine_unstructured(
    store: EncryptedStateStore, clock: FakeClock
) -> None:
    engine = LifeInterview(store.memory, "user")
    reply = engine.begin(context(clock, "begin"))
    reply = skip_to(engine, reply, "routines.sleep", clock)
    reply = answer(engine, reply, "I sleep 10pm-6am daily", clock)
    assert reply.question is not None
    assert reply.question.mode == "confirm"
    reply = engine.advance(
        reply.revision, "skip", "", context(clock, "skip"), question_key=reply.question.key
    )
    assert not LifeModel(store.memory).planning_knowledge("user", clock.now())
    facts = LifeModel(store.memory).knowledge("user", RetrievalScope.PRIVATE, clock.now())
    sleep = next(
        f
        for f in facts
        if f.record.knowledge is not None and f.record.knowledge.key == "routines.sleep"
    )
    assert sleep.record.knowledge is not None
    assert not sleep.record.knowledge.planning_allowed
    assert sleep.record.knowledge.pending_confirmation is None


def test_correcting_confirmation_reparses_and_reconfirms(
    store: EncryptedStateStore, clock: FakeClock
) -> None:
    engine = LifeInterview(store.memory, "user")
    reply = engine.begin(context(clock, "begin"))
    reply = skip_to(engine, reply, "routines.sleep", clock)
    reply = answer(engine, reply, "I sleep at 10pm daily for 8 hours", clock)
    assert reply.question is not None
    assert reply.question.mode == "confirm"
    assert "10:00 PM" in reply.question.prompt
    reply = answer(engine, reply, "Actually 11pm", clock)
    assert reply.question is not None
    assert reply.question.mode == "confirm"
    assert "11:00 PM" in reply.question.prompt
    reply = answer(engine, reply, "yes", clock)
    planning = LifeModel(store.memory).planning_knowledge("user", clock.now())
    assert len(planning) == 1
    assert planning[0].record.knowledge is not None
    routine = planning[0].record.knowledge.routine
    assert routine is not None
    assert routine.start_time == time(23, 0)


def test_unknown_routine_answer_is_not_planning_knowledge(
    store: EncryptedStateStore, clock: FakeClock
) -> None:
    engine = LifeInterview(store.memory, "user")
    reply = engine.begin(context(clock, "begin"))
    reply = skip_to(engine, reply, "routines.sleep", clock)
    reply = answer(engine, reply, "I don't know", clock)
    assert not LifeModel(store.memory).planning_knowledge("user", clock.now())
    facts = LifeModel(store.memory).knowledge("user", RetrievalScope.PRIVATE, clock.now())
    sleep = next(
        f
        for f in facts
        if f.record.knowledge is not None and f.record.knowledge.key == "routines.sleep"
    )
    assert sleep.state is KnowledgeState.UNKNOWN
    assert sleep.record.knowledge is not None
    assert not sleep.record.knowledge.planning_allowed


def test_routine_timezone_comes_from_context(store: EncryptedStateStore, clock: FakeClock) -> None:
    class LocalContext(TransitionContext):
        timezone: str = "America/New_York"

    def local_context(correlation_id: str) -> LocalContext:
        return LocalContext(
            actor=ActorId("user"),
            correlation_id=CorrelationId(correlation_id),
            occurred_at=clock.now(),
            timezone="America/New_York",
        )

    engine = LifeInterview(store.memory, "user")
    reply = engine.begin(context(clock, "begin"))
    reply = skip_to(engine, reply, "routines.sleep", clock)
    assert reply.question is not None
    reply = engine.advance(
        reply.revision,
        "answer",
        "I sleep at 10pm daily for 8 hours",
        local_context("answer"),
        question_key=reply.question.key,
    )
    assert reply.question is not None
    assert reply.question.mode == "confirm"
    assert "America/New_York" in reply.question.prompt
    reply = engine.advance(
        reply.revision,
        "answer",
        "yes",
        local_context("confirm"),
        question_key=reply.question.key,
    )
    planning = LifeModel(store.memory).planning_knowledge("user", clock.now())
    assert len(planning) == 1
    assert planning[0].record.knowledge is not None
    routine = planning[0].record.knowledge.routine
    assert routine is not None
    assert routine.timezone == "America/New_York"


@pytest.mark.parametrize(
    ("text", "missing"),
    [
        ("I sleep well", ("days", "duration", "start time")),
        ("I exercise in the morning daily for 30 minutes", ("start time",)),
        ("I run at 7am for 30 minutes", ("days",)),
        ("I run at 7am daily with 20 minutes commute", ("duration",)),
    ],
)
def test_missing_routine_facts_cannot_be_confirmed_into_a_schedule(
    store: EncryptedStateStore, clock: FakeClock, text: str, missing: tuple[str, ...]
) -> None:
    engine = LifeInterview(store.memory, "user")
    reply = skip_to(engine, engine.begin(context(clock, "begin")), "routines.sleep", clock)
    reply = answer(engine, reply, text, clock)
    assert reply.question is not None
    assert all(field in reply.question.prompt for field in missing)
    reply = answer(engine, reply, "yes", clock)
    assert "missing scheduling details" in reply.acknowledgment
    assert not LifeModel(store.memory).planning_knowledge("user", clock.now())


def test_missing_duration_is_supplied_through_correction_without_losing_original_evidence(
    store: EncryptedStateStore, clock: FakeClock
) -> None:
    engine = LifeInterview(store.memory, "user", timezone="America/Chicago")
    reply = skip_to(engine, engine.begin(context(clock, "begin")), "routines.sleep", clock)
    reply = answer(engine, reply, "I sleep at 10pm daily", clock)
    reply = answer(engine, reply, "for 8 hours", clock)
    assert reply.question is not None
    assert "480 minutes" in reply.question.prompt
    assert "America/Chicago" in reply.question.prompt
    clock.advance(timedelta(minutes=1))
    reply = answer(engine, reply, "yes", clock)
    (view,) = LifeModel(store.memory).planning_knowledge("user", clock.now())
    assert "I sleep at 10pm daily" in view.record.content
    assert "for 8 hours" in view.record.content
    assert view.record.provenance.source is MemorySource.USER_CORRECTION
    assert view.record.knowledge is not None
    assert view.record.knowledge.last_confirmed_at == clock.now()
    assert view.record.knowledge.routine is not None
    assert view.record.knowledge.routine.duration_minutes == 480
    assert (
        view.record.knowledge.routine.priority == 5
    )  # neutral planner policy, not inferred urgency


def test_legacy_unconfirmed_defaults_become_gaps_instead_of_trusted_facts() -> None:
    draft = RoutineDraft.model_validate(
        {
            "days": list(range(7)),
            "duration_minutes": 60,
            "start_time": "08:00:00",
            "timezone": "UTC",
            "flexibility": "preferred",
            "desired": False,
            "priority": 8,
            "can_move": False,
        }
    )
    assert draft.days is None
    assert draft.duration_minutes is None
    assert draft.start_time is None
    assert draft.missing()


@pytest.mark.parametrize(
    ("description", "expected_days"),
    [
        ("Monday through Friday", frozenset(range(5))),
        ("daily except weekends", frozenset(range(5))),
    ],
)
def test_explicit_day_ranges_and_exclusions(
    store: EncryptedStateStore, clock: FakeClock, description: str, expected_days: frozenset[int]
) -> None:
    engine = LifeInterview(store.memory, "user")
    reply = skip_to(engine, engine.begin(context(clock, "begin")), "routines.sleep", clock)
    _ = confirm_routine(engine, reply, f"I sleep 10pm-6am {description}", clock)
    (view,) = LifeModel(store.memory).planning_knowledge("user", clock.now())
    assert view.record.knowledge is not None
    assert view.record.knowledge.routine is not None
    assert view.record.knowledge.routine.days == expected_days
