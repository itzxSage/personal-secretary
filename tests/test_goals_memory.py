"""Goals, quests, and governed memory contract tests."""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from secretary_service.goals import (
    Dream,
    GoalGraphNode,
    Milestone,
    Project,
    Quest,
)
from secretary_service.goals import Goal as GraphGoal
from secretary_service.goals import Task as GraphTask
from secretary_service.memory import (
    MemoryCategory,
    MemoryCorrection,
    MemoryDeletion,
    MemoryProvenance,
    MemoryRecord,
    MemorySource,
    RetrievalScope,
    StaleMemoryRevisionError,
)
from secretary_service.models import Goal, RecordKind, Task
from secretary_service.storage import EncryptedStateStore
from tests.goals_memory_helpers import context, memory_record, record_id
from tests.helpers import FakeClock


def test_baseline_goal_and_task_roundtrip_remains_audited(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    """Characterize the pre-Todo-6 goal/task persistence seam."""
    goal_id = record_id(1)
    task_id = record_id(2)
    transition = context(clock, "corr-task-6-baseline")

    store.create(
        Goal(record_id=goal_id, created_at=clock.now(), state="active", title="Ship LifeOS"),
        transition,
    )
    store.create(
        Task(record_id=task_id, created_at=clock.now(), state="active", title="Model quests"),
        transition,
    )

    assert store.read(RecordKind.GOAL, goal_id) is not None
    assert store.read(RecordKind.TASK, task_id) is not None
    assert [entry.action_class for entry in store.audit_entries()] == [
        "goal.created",
        "task.created",
    ]
    assert store.verify_audit_chain().entries_verified == 2


def test_task_explanation_resolves_the_canonical_quest_and_full_lineage(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    transition = context(clock, "corr-goal-graph")
    nodes: tuple[GoalGraphNode, ...] = (
        Dream(record_id=record_id(10), created_at=clock.now(), title="Support my family"),
        GraphGoal(
            record_id=record_id(11),
            created_at=clock.now(),
            title="Reach a stronger career",
            dream_id=record_id(10),
        ),
        Milestone(
            record_id=record_id(12),
            created_at=clock.now(),
            title="Move into AI operations",
            goal_id=record_id(11),
        ),
        Project(
            record_id=record_id(13),
            created_at=clock.now(),
            title="Career transition",
            milestone_id=record_id(12),
        ),
        Quest(
            record_id=record_id(14),
            created_at=clock.now(),
            title="Land an AI-enabled role",
            project_id=record_id(13),
        ),
        GraphTask(
            record_id=record_id(15),
            created_at=clock.now(),
            title="Finish portfolio README",
            quest_id=record_id(14),
        ),
    )
    for node in nodes:
        store.goals.add(node, transition)

    explanation = store.goals.explain_task(record_id(15))

    assert explanation.canonical_quest.record_id == record_id(14)
    assert explanation.task.quest_id == explanation.canonical_quest.record_id
    assert explanation.lineage == tuple(node.record_id for node in nodes)


def test_goal_graph_rejects_a_parent_of_the_wrong_canonical_kind(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    transition = context(clock, "corr-invalid-graph")
    store.goals.add(
        Dream(record_id=record_id(20), created_at=clock.now(), title="Wrong parent"),
        transition,
    )

    with pytest.raises(ValueError, match="goal parent"):
        store.goals.add(
            Quest(
                record_id=record_id(21),
                created_at=clock.now(),
                title="Malformed quest",
                project_id=record_id(20),
            ),
            transition,
        )


@pytest.mark.parametrize("category", list(MemoryCategory))
def test_memory_categories_preserve_governance_metadata_and_scope_retrieval(
    store: EncryptedStateStore,
    clock: FakeClock,
    category: MemoryCategory,
) -> None:
    memory_id = record_id(100 + list(MemoryCategory).index(category))
    record = memory_record(
        clock,
        memory_id,
        category,
        f"governed {category.value}",
        RetrievalScope.CONVERSATION,
        RetrievalScope.PLANNING,
    )
    store.memory.remember(record, context(clock, f"corr-{category.value}"))

    planning = store.memory.retrieve(RetrievalScope.PLANNING)
    private = store.memory.retrieve(RetrievalScope.PRIVATE)

    assert planning == (record,)
    assert private == ()
    assert planning[0].provenance.source_id == "conversation:event:42"
    assert planning[0].confidence == 0.95


def test_user_correction_replaces_retrieval_and_retains_only_a_fingerprint_in_audit(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    memory_id = record_id(200)
    original = memory_record(
        clock,
        memory_id,
        MemoryCategory.PREFERENCE,
        "Prefers evening workouts",
        RetrievalScope.PLANNING,
    )
    store.memory.remember(original, context(clock, "corr-memory-create"))
    correction = MemoryCorrection(
        expected_revision=1,
        content="Prefers morning workouts",
        provenance=MemoryProvenance(
            source=MemorySource.USER_CORRECTION,
            source_id="conversation:event:43",
            captured_at=clock.now(),
        ),
        confidence=1.0,
        retrieval_scopes=frozenset({RetrievalScope.PLANNING}),
    )

    corrected = store.memory.correct(
        memory_id,
        correction,
        context(clock, "corr-memory-correct"),
    )

    assert corrected.revision == 2
    assert [item.content for item in store.memory.retrieve(RetrievalScope.PLANNING)] == [
        "Prefers morning workouts"
    ]
    audit = store.audit_entries()[-1]
    assert audit.action_class == "memory.corrected"
    assert len(audit.source_fingerprint) == 64
    assert "evening workouts" not in audit.model_dump_json()
    assert store.memory.tombstones(memory_id)[0].source_fingerprint == audit.source_fingerprint


def test_user_deletion_hides_memory_and_rejects_a_stale_revision(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    memory_id = record_id(300)
    record = memory_record(
        clock,
        memory_id,
        MemoryCategory.COMMITMENT,
        "Call the school Friday",
        RetrievalScope.PRIVATE,
    )
    store.memory.remember(record, context(clock, "corr-delete-create"))

    with pytest.raises(StaleMemoryRevisionError):
        store.memory.delete(
            memory_id,
            MemoryDeletion(expected_revision=2),
            context(clock, "corr-delete-stale"),
        )
    assert store.memory.retrieve(RetrievalScope.PRIVATE) == (record,)

    store.memory.delete(
        memory_id,
        MemoryDeletion(expected_revision=1),
        context(clock, "corr-delete-current"),
    )

    assert store.memory.retrieve(RetrievalScope.PRIVATE) == ()
    assert store.memory.tombstones(memory_id)[0].reason == "user_deletion"
    assert store.audit_entries()[-1].action_class == "memory.deleted"


def test_retention_expiry_removes_memory_from_retrieval_and_can_be_purged(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    memory_id = record_id(400)
    record = memory_record(
        clock,
        memory_id,
        MemoryCategory.EPISODE,
        "Temporary episode",
        RetrievalScope.CONVERSATION,
    ).model_copy(update={"retain_until": clock.now() + timedelta(hours=1)})
    store.memory.remember(record, context(clock, "corr-retention-create"))
    clock.advance(timedelta(hours=2))

    assert store.memory.retrieve(RetrievalScope.CONVERSATION) == ()
    assert store.memory.purge_expired(context(clock, "corr-retention-purge")) == 1
    assert store.memory.tombstones(memory_id)[0].reason == "retention_expired"


def test_malformed_memory_is_rejected_at_the_model_boundary(clock: FakeClock) -> None:
    record = memory_record(
        clock,
        record_id(500),
        MemoryCategory.OBSERVATION,
        "Invalid confidence",
        RetrievalScope.PRIVATE,
    )
    with pytest.raises(ValidationError):
        _ = MemoryRecord.model_validate(record.model_dump() | {"confidence": 1.5})
