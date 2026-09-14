"""Evidence, temporal validity, privacy and correction regressions."""

from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from secretary_service.backups import BackupInvalidatedError, BackupManager
from secretary_service.keys import DeterministicTestKeyProvider
from secretary_service.life_knowledge import (
    KnowledgeDetails,
    KnowledgeKind,
    KnowledgeState,
    LifeDomain,
)
from secretary_service.life_model import LifeModel
from secretary_service.memory import (
    MemoryCategory,
    MemoryDeletion,
    MemoryProvenance,
    MemoryRecord,
    MemorySource,
    RetrievalScope,
)
from secretary_service.storage import EncryptedStateStore
from tests.goals_memory_helpers import context, record_id
from tests.helpers import FakeClock


def assertion(clock: FakeClock, number: int = 1, **updates: object) -> MemoryRecord:
    details = KnowledgeDetails(
        subject_id="user",
        key="work.employer",
        domain=LifeDomain.WORK,
        kind=KnowledgeKind.FACT,
        state=KnowledgeState.CONFIRMED,
        observed_at=clock.now(),
        last_confirmed_at=clock.now(),
        expected_staleness_days=30,
        planning_allowed=True,
    )
    return MemoryRecord.model_validate(
        {
            "memory_id": record_id(number),
            "category": MemoryCategory.PROFILE,
            "content": "Company A",
            "confidence": 1,
            "provenance": MemoryProvenance(
                source=MemorySource.USER_STATEMENT, source_id="interview:1", captured_at=clock.now()
            ),
            "retrieval_scopes": frozenset({RetrievalScope.PRIVATE, RetrievalScope.PLANNING}),
            "created_at": clock.now(),
            "knowledge": details,
        }
        | updates
    )


def test_inference_cannot_be_promoted_to_confirmed_by_import_or_unchecked_copy(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    record = assertion(clock)
    forged = record.model_copy(
        update={
            "provenance": MemoryProvenance(
                source=MemorySource.INFERENCE,
                source_id="pattern:1",
                captured_at=clock.now(),
            )
        }
    )
    with pytest.raises(ValidationError, match="explicit user provenance"):
        store.memory.remember(forged, context(clock, "inference"))
    assert store.memory.retrieve(RetrievalScope.PRIVATE) == ()


def test_staleness_does_not_rewrite_history_and_excludes_planning(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    record = assertion(clock)
    store.memory.remember(record, context(clock, "create"))
    model = LifeModel(store.memory)
    assert len(model.planning_knowledge("user", clock.now())) == 1
    later = clock.now() + timedelta(days=30)
    view = model.knowledge("user", RetrievalScope.PRIVATE, later)[0]
    assert view.state is KnowledgeState.STALE
    assert "outdated" in view.explanation()
    assert model.planning_knowledge("user", later) == ()
    assert store.memory.retrieve(RetrievalScope.PRIVATE)[0] == record


def test_conflicts_are_not_silently_resolved_even_if_one_source_is_private(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    first = assertion(clock)
    second = assertion(clock, 2, content="Company B", retrieval_scopes={RetrievalScope.PRIVATE})
    for record in (first, second):
        store.memory.remember(record, context(clock, "source"))
    views = LifeModel(store.memory).knowledge("user", RetrievalScope.PRIVATE, clock.now())
    assert all(view.state is KnowledgeState.CONFLICTED for view in views)
    assert LifeModel(store.memory).planning_knowledge("user", clock.now()) == ()


def test_validity_keeps_historical_employer_distinct_from_current(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    first = assertion(clock)
    assert first.knowledge is not None
    historical = first.model_copy(
        update={
            "knowledge": first.knowledge.model_copy(
                update={
                    "valid_until": clock.now(),
                }
            )
        }
    )
    current = assertion(clock, 2, content="Company B")
    for record in (historical, current):
        store.memory.remember(record, context(clock, "source"))
    views = LifeModel(store.memory).knowledge("user", RetrievalScope.PRIVATE, clock.now())
    assert not views[0].current
    assert views[1].state is KnowledgeState.CONFIRMED
    assert len(LifeModel(store.memory).planning_knowledge("user", clock.now())) == 1


def test_forgetting_erases_content_and_prevents_identity_resurrection(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    record = assertion(clock)
    store.memory.remember(record, context(clock, "create"))
    store.memory.delete(
        record.memory_id, MemoryDeletion(expected_revision=1), context(clock, "forget")
    )
    with pytest.raises(ValueError, match="forgotten"):
        store.memory.remember(record, context(clock, "reimport"))
    assert LifeModel(store.memory).knowledge("user", RetrievalScope.PRIVATE, clock.now()) == ()
    assert "Company A" not in str(store.audit_entries())


def test_nested_memory_transaction_rolls_back_answers_and_audit(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    def fail_after_answer() -> None:
        with store.memory.transaction():
            store.memory.remember(assertion(clock), context(clock, "answer"))
            raise RuntimeError

    with pytest.raises(RuntimeError):
        fail_after_answer()
    assert not store.memory.retrieve(RetrievalScope.PRIVATE)
    assert not store.audit_entries()


def test_forgotten_knowledge_cannot_be_restored_from_a_backup(
    store: EncryptedStateStore,
    clock: FakeClock,
    keys: DeterministicTestKeyProvider,
    tmp_path: Path,
) -> None:
    record = assertion(clock)
    store.memory.remember(record, context(clock, "create"))
    backups = BackupManager(store, tmp_path / "backups", keys, clock)
    backup = backups.create(context(clock, "backup"))
    store.memory.delete(
        record.memory_id, MemoryDeletion(expected_revision=1), context(clock, "forget")
    )
    with pytest.raises(BackupInvalidatedError):
        _ = backups.verify_restore(backup.backup_id)
