"""Pure confidence-qualified learning aggregation."""

from collections import defaultdict
from collections.abc import Hashable
from datetime import datetime
from typing import Final
from uuid import UUID, uuid5

from secretary_service.learning.models import (
    ActualOutcome,
    LearningObservation,
    LearningProposal,
    MemoryUpdateProposal,
    ObservationTrigger,
    SkillProposal,
)
from secretary_service.memory import MemoryCategory
from secretary_service.models import NonEmpty, RecordId

MINIMUM_OBSERVATIONS: Final = 3
FULL_CONFIDENCE_OBSERVATIONS: Final = 5
MINIMUM_PROPOSAL_CONFIDENCE: Final = 0.6
LEARNING_NAMESPACE: Final = UUID("6f1e2d3c-4b5a-4c7d-9e8f-0a1b2c3d4e5f")

_MemoryGroupKey = tuple[ActualOutcome, tuple[NonEmpty, ...]]


def _proposal_id(*parts: str) -> UUID:
    """Derive a deterministic content-addressed proposal identifier."""
    return uuid5(LEARNING_NAMESPACE, "\x1f".join(parts))


def _most_common[T: Hashable](values: tuple[T, ...]) -> T:
    """Return the first most frequent value for deterministic tie-breaking."""
    counts: dict[T, int] = defaultdict(int)
    for value in values:
        counts[value] += 1
    return max(counts, key=lambda item: counts[item])


def _learn_procedure_proposals(
    observations: tuple[LearningObservation, ...],
    learned_at: datetime,
) -> tuple[SkillProposal, ...]:
    """Propose a procedure when repeated completed outcomes share steps."""
    grouped: dict[ObservationTrigger, list[LearningObservation]] = defaultdict(list)
    for item in observations:
        if (
            item.outcome is ActualOutcome.COMPLETED
            and item.trigger is not None
            and item.procedure_steps
        ):
            grouped[item.trigger].append(item)
    proposals: list[SkillProposal] = []
    for trigger, samples in grouped.items():
        if len(samples) < MINIMUM_OBSERVATIONS:
            continue
        steps = _most_common(tuple(sample.procedure_steps for sample in samples))
        agreement = sum(sample.procedure_steps == steps for sample in samples) / len(samples)
        confidence = round(agreement * min(1.0, len(samples) / FULL_CONFIDENCE_OBSERVATIONS), 6)
        if confidence < MINIMUM_PROPOSAL_CONFIDENCE:
            continue
        sources = {sample.provenance.source.value for sample in samples}
        proposals.append(
            SkillProposal(
                proposal_id=RecordId(_proposal_id("procedure", trigger.kind, trigger.source)),
                trigger=trigger,
                procedure=steps,
                supporting_observation_ids=tuple(
                    sorted(sample.observation_id for sample in samples)
                ),
                confidence=confidence,
                explanation=(
                    f"{len(samples)} completed {trigger.kind} observations from "
                    f"{', '.join(sorted(sources))} followed the same "
                    f"{len(steps)}-step procedure"
                ),
                created_at=learned_at,
            )
        )
    return tuple(proposals)


def _learn_memory_update_proposals(
    observations: tuple[LearningObservation, ...],
    learned_at: datetime,
) -> tuple[MemoryUpdateProposal, ...]:
    """Propose a memory update when repeated outcomes diverge from the plan."""
    grouped: dict[_MemoryGroupKey, list[LearningObservation]] = defaultdict(list)
    for item in observations:
        if item.outcome is not ActualOutcome.COMPLETED:
            grouped[(item.outcome, item.context_tags)].append(item)
    proposals: list[MemoryUpdateProposal] = []
    for (outcome, context_tags), samples in grouped.items():
        if len(samples) < MINIMUM_OBSERVATIONS:
            continue
        confidence = round(min(1.0, len(samples) / FULL_CONFIDENCE_OBSERVATIONS), 6)
        if confidence < MINIMUM_PROPOSAL_CONFIDENCE:
            continue
        title = _most_common(tuple(sample.planned.title for sample in samples))
        context = ", ".join(context_tags)
        content = (
            f"{title} is usually {outcome.value} after {context}"
            if context
            else f"{title} is usually {outcome.value}"
        )
        sources = {sample.provenance.source.value for sample in samples}
        proposals.append(
            MemoryUpdateProposal(
                proposal_id=RecordId(_proposal_id("memory_update", outcome.value, *context_tags)),
                category=MemoryCategory.OBSERVATION,
                content=content,
                supporting_observation_ids=tuple(
                    sorted(sample.observation_id for sample in samples)
                ),
                confidence=confidence,
                explanation=(
                    f"{len(samples)} {outcome.value} observations from "
                    f"{', '.join(sorted(sources))} share the same outcome"
                ),
                created_at=learned_at,
            )
        )
    return tuple(proposals)


def learn_proposals(
    observations: tuple[LearningObservation, ...],
    learned_at: datetime,
) -> tuple[LearningProposal, ...]:
    """Aggregate repeated outcomes into explainable, never-activated proposals."""
    procedures = _learn_procedure_proposals(observations, learned_at)
    memory_updates = _learn_memory_update_proposals(observations, learned_at)
    return procedures + memory_updates
