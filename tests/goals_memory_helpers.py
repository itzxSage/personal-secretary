"""Typed fixtures shared by goals and governed-memory tests."""

from uuid import UUID

from secretary_service.memory import (
    MemoryCategory,
    MemoryProvenance,
    MemoryRecord,
    MemorySource,
    RetrievalScope,
)
from secretary_service.models import ActorId, CorrelationId, RecordId, TransitionContext
from tests.helpers import FakeClock


def record_id(suffix: int) -> RecordId:
    return RecordId(UUID(f"60000000-0000-0000-0000-{suffix:012d}"))


def context(clock: FakeClock, correlation_id: str) -> TransitionContext:
    return TransitionContext(
        actor=ActorId("user"),
        correlation_id=CorrelationId(correlation_id),
        occurred_at=clock.now(),
    )


def memory_record(
    clock: FakeClock,
    memory_id: RecordId,
    category: MemoryCategory,
    content: str,
    *scopes: RetrievalScope,
) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        category=category,
        content=content,
        provenance=MemoryProvenance(
            source=MemorySource.USER_STATEMENT,
            source_id="conversation:event:42",
            captured_at=clock.now(),
        ),
        confidence=0.95,
        retrieval_scopes=frozenset(scopes),
        created_at=clock.now(),
    )
