"""Explicit user corrections and retrieval restrictions for the Life Model."""

from datetime import datetime
from typing import Literal

from pydantic import Field

from secretary_service.life_knowledge import (
    KnowledgeDetails,
    KnowledgeModel,
    KnowledgeState,
    Sensitivity,
)
from secretary_service.life_model import LifeModel
from secretary_service.memory import (
    MemoryCorrection,
    MemoryDeletion,
    MemoryProvenance,
    MemorySource,
    RetrievalScope,
    StaleMemoryRevisionError,
)
from secretary_service.models import RecordId, TransitionContext


class KnowledgeCommand(KnowledgeModel):
    """One selected record and exact revision; preferences cannot authorize tools."""

    expected_revision: int = Field(ge=1)
    action: Literal[
        "correct",
        "confirm",
        "private",
        "exclude_planning",
        "allow_planning",
        "no_longer_true",
        "temporary",
        "forget",
    ]
    text: str = Field(default="", max_length=8000)
    valid_until: datetime | None = None


class KnowledgeItem(KnowledgeModel):
    """Human inspection projection assembled from structured evidence."""

    memory_id: RecordId
    revision: int
    domain: str
    content: str
    state: KnowledgeState
    current: bool
    explanation: str
    sensitivity: Sensitivity
    planning_allowed: bool


def knowledge_items(model: LifeModel, subject_id: str, now: datetime) -> tuple[KnowledgeItem, ...]:
    """Show only this person's current and historical governed assertions."""
    return tuple(
        KnowledgeItem(
            memory_id=view.record.memory_id,
            revision=view.record.revision,
            domain=details.domain.value,
            content=view.record.content,
            state=view.state,
            current=view.current,
            explanation=view.explanation(),
            sensitivity=details.sensitivity,
            planning_allowed=details.planning_allowed,
        )
        for view in model.knowledge(subject_id, RetrievalScope.PRIVATE, now)
        if (details := view.record.knowledge) is not None
    )


def correct_knowledge(
    model: LifeModel,
    subject_id: str,
    memory_id: RecordId,
    command: KnowledgeCommand,
    context: TransitionContext,
) -> None:
    """Apply explicit changes, preserving evidence and physical-erasure guarantees."""
    with model.memory.transaction():
        found = next(
            (
                view
                for view in model.knowledge(subject_id, RetrievalScope.PRIVATE, context.occurred_at)
                if view.record.memory_id == memory_id
            ),
            None,
        )
        if found is None or found.record.knowledge is None:
            message = "knowledge unavailable"
            raise ValueError(message)
        record = found.record
        if record.revision != command.expected_revision:
            raise StaleMemoryRevisionError(memory_id, command.expected_revision, record.revision)
        if command.action == "forget":
            model.memory.delete(
                memory_id, MemoryDeletion(expected_revision=record.revision), context
            )
            return
        details = found.record.knowledge
        updates: dict[str, object] = {}
        content = record.content
        scopes = record.retrieval_scopes
        confidence = record.confidence
        if command.action in {"correct", "confirm"}:
            if command.action == "correct":
                if not command.text.strip():
                    message = "a correction requires the replacement fact"
                    raise ValueError(message)
                content = command.text.strip()
            updates = {
                "state": KnowledgeState.CONFIRMED,
                "last_confirmed_at": context.occurred_at,
                "observed_at": context.occurred_at,
                "conflicts_with": (),
                "valid_from": context.occurred_at,
                "valid_until": None,
            }
            confidence = 1
        elif command.action == "private":
            scopes = frozenset({RetrievalScope.PRIVATE})
            updates = {"sensitivity": Sensitivity.RESTRICTED, "planning_allowed": False}
        elif command.action in {"exclude_planning", "allow_planning"}:
            allowed = command.action == "allow_planning"
            updates["planning_allowed"] = allowed
            scopes = (
                scopes | {RetrievalScope.PLANNING}
                if allowed
                else scopes - {RetrievalScope.PLANNING}
            )
        elif command.action in {"no_longer_true", "temporary"}:
            updates["valid_until"] = _valid_until(command, context.occurred_at)
        replacement = KnowledgeDetails.model_validate(details.model_dump() | updates)
        _ = model.memory.correct(
            memory_id,
            MemoryCorrection(
                expected_revision=record.revision,
                content=content,
                provenance=MemoryProvenance(
                    source=MemorySource.USER_CORRECTION,
                    source_id=f"correction:{context.correlation_id}",
                    captured_at=context.occurred_at,
                ),
                confidence=confidence,
                retrieval_scopes=scopes,
                retain_until=record.retain_until,
                knowledge=replacement,
            ),
            context,
        )


def _valid_until(command: KnowledgeCommand, now: datetime) -> datetime:
    until = now if command.action == "no_longer_true" else command.valid_until
    if until is None:
        message = "temporary knowledge requires an end time"
        raise ValueError(message)
    return until
