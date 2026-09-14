"""Synthetic source-only tests; never copy a real user's bootstrap into fixtures."""

import json

import pytest
import yaml

from secretary_service.knowledge_import import ImportSummary, import_knowledge
from secretary_service.life_interview import LifeInterview
from secretary_service.life_knowledge import KnowledgeState, Sensitivity
from secretary_service.life_model import LifeModel
from secretary_service.memory import MemoryDeletion, MemoryRecord, MemorySource, RetrievalScope
from secretary_service.memory_repository import MemoryRepository
from secretary_service.models import TransitionContext
from secretary_service.storage import EncryptedStateStore
from tests.goals_memory_helpers import context
from tests.helpers import FakeClock
from tests.test_life_interview import answer
from tests.test_life_knowledge import assertion


def source() -> bytes:
    # JSON is a YAML subset and keeps the synthetic fixture easy to inspect.
    return json.dumps(
        {
            "schema": {
                "name": "LifeOSUserBootstrap",
                "version": "1.0",
                "generated_at": "2025-01-01",
            },
            "career": {
                "current_employment": {
                    "last_known": {
                        "employer": "Example Workshop",
                        "role": "Designer",
                        "status": "STALE",
                        "confidence": 0.8,
                        "source": "older conversation",
                        "valid_until": "2025-06-01T00:00:00+00:00",
                        "observed_as_of": "2024-07",
                    }
                }
            },
            "goals": [
                {
                    "title": "Learn a craft",
                    "status": "CONFIRMED",
                    "confidence": 0.9,
                    "related_project_ids": ["source-project-a"],
                }
            ],
            "relationships": {
                "value": "Private context",
                "status": "INFERRED",
                "confidence": 0.4,
                "sensitivity": "high",
            },
            "current_uncertainties": [
                {"key": "work.schedule.next_7_days", "status": "UNKNOWN", "impact": "high"}
            ],
            "subject": {
                "identity": {
                    "base": {"value": "Example town", "status": "CONFLICTED", "confidence": 0.5},
                    "pattern": {
                        "value": "Observed pattern",
                        "status": "OBSERVED",
                        "confidence": 0.85,
                    },
                }
            },
            "candidate_open_loops": {"items": ["Ask about a repair"]},
        }
    ).encode()


def ingest(store: EncryptedStateStore, clock: FakeClock, raw: bytes | None = None) -> ImportSummary:
    return import_knowledge(
        raw or source(), store.memory, "user", "synthetic_bootstrap_v1", context(clock, "import")
    )


def test_import_states_privacy_provenance_and_idempotence(
    store: EncryptedStateStore, clock: FakeClock
) -> None:
    result = ingest(store, clock)
    assert result.imported == 7
    assert result.duplicates == 0
    assert result.stale == 1
    assert result.confirmation_claims_pending == 1
    facts = store.memory.retrieve(RetrievalScope.PRIVATE)
    assert {f.knowledge.state for f in facts if f.knowledge} == {
        KnowledgeState.STALE,
        KnowledgeState.UNKNOWN,
        KnowledgeState.INFERRED,
        KnowledgeState.CONFLICTED,
        KnowledgeState.OBSERVED,
    }
    for item in facts:
        assert item.provenance.source is MemorySource.IMPORT
        assert item.provenance.source_id == "synthetic_bootstrap_v1"
        assert item.retrieval_scopes == frozenset({RetrievalScope.PRIVATE})
        assert item.knowledge is not None
        assert not item.knowledge.planning_allowed
        assert item.knowledge.source_evidence is not None
    job = next(f for f in facts if "Example Workshop" in f.content)
    assert job.knowledge is not None
    assert job.knowledge.observed_at.year == 2024
    assert job.knowledge.observed_at.month == 7
    assert job.knowledge.source_evidence is not None
    assert job.knowledge.source_evidence.observation_precision == "month"
    private = next(f for f in facts if f.content == "Private context")
    assert private.knowledge is not None
    assert private.knowledge.sensitivity is Sensitivity.RESTRICTED
    goal = next(f for f in facts if f.content == "Learn a craft")
    assert goal.knowledge is not None
    assert goal.knowledge.source_evidence is not None
    assert goal.knowledge.source_evidence.original["related_project_ids"] == ["source-project-a"]
    assert goal.knowledge.last_confirmed_at is None
    assert not LifeModel(store.memory).planning_knowledge("user", clock.now())
    again = ingest(store, clock)
    assert again.imported == 0
    assert again.duplicates == 7
    assert len(store.memory.retrieve(RetrievalScope.PRIVATE)) == 7


def test_changed_version_fails_and_deleted_assertion_stays_deleted(
    store: EncryptedStateStore, clock: FakeClock
) -> None:
    _ = ingest(store, clock)
    with pytest.raises(ValueError, match="different content"):
        _ = ingest(store, clock, source().replace(b"Designer", b"Editor"))
    record = store.memory.retrieve(RetrievalScope.PRIVATE)[0]
    store.memory.delete(
        record.memory_id,
        MemoryDeletion(expected_revision=record.revision),
        context(clock, "forget"),
    )
    assert ingest(store, clock).imported == 0
    assert record.memory_id not in store.memory.record_ids()


def test_mid_import_failure_rolls_back_records_and_audit(
    store: EncryptedStateStore, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    remember = MemoryRepository.remember
    calls = 0

    def fail(self: MemoryRepository, record: MemoryRecord, ctx: TransitionContext) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError
        remember(self, record, ctx)

    monkeypatch.setattr(MemoryRepository, "remember", fail)
    with pytest.raises(RuntimeError):
        _ = ingest(store, clock)
    assert store.memory.retrieve(RetrievalScope.PRIVATE) == ()
    monkeypatch.setattr(MemoryRepository, "remember", remember)
    assert ingest(store, clock).imported == 7


@pytest.mark.parametrize(
    "raw", [b"bad: [", b"schema: &a {name: a}\ncopy: *a", source().replace(b"STALE", b"FAKE")]
)
def test_malformed_sources_leave_store_unchanged(
    store: EncryptedStateStore, clock: FakeClock, raw: bytes
) -> None:
    with pytest.raises((ValueError, yaml.YAMLError)):
        _ = ingest(store, clock, raw)
    assert store.memory.retrieve(RetrievalScope.PRIVATE) == ()


def test_week_interview_reviews_history_then_keeps_both_provenances(
    store: EncryptedStateStore, clock: FakeClock
) -> None:
    _ = ingest(store, clock)
    engine = LifeInterview(store.memory, "user")
    reply = engine.begin(context(clock, "begin"), objective="week_planning")
    assert reply.question is not None
    assert reply.question.key == "work.role"
    assert reply.question.mode == "review"
    assert "Example Workshop" in reply.question.prompt
    assert "outdated" in reply.question.prompt
    reply = answer(engine, reply, "I now work at Example Studio as an editor", clock)
    assert reply.question is not None
    assert reply.question.key == "work.schedule"
    views = LifeModel(store.memory).knowledge("user", RetrievalScope.PRIVATE, clock.now())
    old = next(v for v in views if "Example Workshop" in v.record.content)
    new = next(v for v in views if "Example Studio" in v.record.content)
    assert not old.current
    assert old.state is KnowledgeState.STALE
    assert old.record.provenance.source is MemorySource.IMPORT
    assert old.record.knowledge is not None
    assert old.record.knowledge.source_evidence is not None
    assert new.state is KnowledgeState.CONFIRMED
    assert new.current
    assert new.record.provenance.source is MemorySource.USER_CORRECTION
    assert new.record.knowledge is not None
    assert new.record.knowledge.last_confirmed_at == clock.now()
    assert ingest(store, clock).imported == 0
    reopened = engine.begin(context(clock, "reopen"))
    assert reopened.question is not None
    assert reopened.question.key == "work.schedule"
    assert not LifeModel(store.memory).planning_knowledge("user", clock.now())


def test_sensitive_history_requires_permission_before_disclosure(
    store: EncryptedStateStore, clock: FakeClock
) -> None:
    _ = ingest(
        store,
        clock,
        source().replace(b'"role": "Designer"', b'"role": "Designer", "compensation": "private"'),
    )
    engine = LifeInterview(store.memory, "user")
    reply = engine.begin(context(clock, "begin"), objective="week_planning")
    assert reply.question is not None
    assert reply.question.key == "permission.work"
    assert "Example Workshop" not in reply.question.prompt
    reply = answer(engine, reply, "yes", clock)
    assert reply.question is not None
    assert reply.question.key == "work.role"
    assert "Example Workshop" in reply.question.prompt


@pytest.mark.parametrize(
    "raw",
    [
        b'career:\n  current_employment:\n    last_known:\n      employer: "Good Corp"\n      employer: "Evil Corp"',
        b'subject:\n  identity:\n    preferred_name:\n      value: "Alice"\n      value: "Bob"',
    ],
)
def test_duplicate_yaml_keys_are_rejected(
    store: EncryptedStateStore, clock: FakeClock, raw: bytes
) -> None:
    with pytest.raises(ValueError, match="duplicate YAML key"):
        _ = ingest(store, clock, raw)
    assert store.memory.retrieve(RetrievalScope.PRIVATE) == ()


def test_current_user_evidence_outranks_bootstrap(
    store: EncryptedStateStore, clock: FakeClock
) -> None:
    current = assertion(clock, content="Current job")
    assert current.knowledge is not None
    current = current.model_copy(
        update={"knowledge": current.knowledge.model_copy(update={"key": "work.role"})}
    )
    store.memory.remember(current, context(clock, "current"))
    _ = ingest(store, clock)
    engine = LifeInterview(store.memory, "user")
    reply = engine.begin(context(clock, "begin"), objective="week_planning")
    assert reply.question is not None
    assert reply.question.key == "work.schedule"
