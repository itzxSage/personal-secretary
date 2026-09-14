"""Persistent, evidence-aware interview behavior shared by voice and text."""

from datetime import timedelta

import pytest

from secretary_service.interview_catalog import TOPICS
from secretary_service.life_interview import InterviewReply, LifeInterview
from secretary_service.life_knowledge import KnowledgeState, LifeDomain
from secretary_service.life_model import LifeModel
from secretary_service.memory import (
    MemoryProvenance,
    MemorySource,
    RetrievalScope,
    StaleMemoryRevisionError,
)
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
    assert not LifeModel(store.memory).planning_knowledge("user", clock.now())


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
    assert len(facts) == 2
    assert all(
        v.record.knowledge is not None
        and v.record.knowledge.open_loop is not None
        and v.record.knowledge.open_loop.disposition == "inbox"
        for v in facts
    )
    assert not LifeModel(store.memory).planning_knowledge("user", clock.now())
