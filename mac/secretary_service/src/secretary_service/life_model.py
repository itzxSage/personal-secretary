"""Purpose-limited projections of structured knowledge, never authority grants."""

from dataclasses import dataclass
from datetime import datetime

from secretary_service.life_knowledge import KnowledgeKind, KnowledgeState, Sensitivity
from secretary_service.memory import MemoryRecord, MemorySource, RetrievalScope
from secretary_service.memory_repository import MemoryRepository

MIN_CONFIDENCE = 0.8


@dataclass(frozen=True)
class KnowledgeView:
    """Evidence with a derived state; conflicting sources remain individually visible."""

    record: MemoryRecord
    state: KnowledgeState
    current: bool

    def explanation(self) -> str:
        """Explain actual provenance without inventing a rationale."""
        provenance = self.record.provenance
        labels = {
            MemorySource.USER_STATEMENT: "You told me this",
            MemorySource.USER_CORRECTION: "You corrected this",
            MemorySource.IMPORT: "This was imported",
            MemorySource.CONNECTED_SYSTEM: "This came from a connected system",
            MemorySource.BEHAVIOR: "This was observed in your activity",
            MemorySource.INFERENCE: "This is an inference you have not confirmed",
            MemorySource.CONVERSATION: "This came from a conversation",
        }
        text = f"{labels[provenance.source]} on {provenance.captured_at.date().isoformat()}."
        if self.state is KnowledgeState.CONFLICTED:
            text += " Sources disagree; I need your clarification."
        elif self.state is KnowledgeState.STALE:
            text += " This may be outdated."
        if not self.current:
            text += " It does not apply to the current time."
        return text


@dataclass(frozen=True)
class LifeModel:
    """Read the existing governed memory repository as a structured Life Model."""

    memory: MemoryRepository

    def knowledge(
        self,
        subject_id: str,
        scope: RetrievalScope,
        now: datetime,
    ) -> tuple[KnowledgeView, ...]:
        """Find overlapping contradictions without silently choosing a winner."""
        records = tuple(
            record
            for record in self.memory.retrieve(scope)
            if record.knowledge is not None and record.knowledge.subject_id == subject_id
        )
        result: list[KnowledgeView] = []
        for record in records:
            details = record.knowledge
            if details is None:
                continue
            state = details.effective_state(now)
            current = details.current_at(now)
            if (
                current
                and details.cardinality == "one"
                and any(
                    other.memory_id != record.memory_id
                    and other.knowledge is not None
                    and other.knowledge.key == details.key
                    and other.knowledge.cardinality == "one"
                    and other.knowledge.current_at(now)
                    and other.knowledge.state is not KnowledgeState.UNKNOWN
                    and details.state is not KnowledgeState.UNKNOWN
                    and other.content.casefold().strip() != record.content.casefold().strip()
                    for other in records
                )
            ):
                state = KnowledgeState.CONFLICTED
            result.append(KnowledgeView(record, state, current))
        return tuple(result)

    def planning_knowledge(self, subject_id: str, now: datetime) -> tuple[KnowledgeView, ...]:
        """Exclude uncertain, private, stale and unclarified inbox assertions."""
        # Inspect all privately visible sources before filtering, so withholding a
        # contradictory source from planning cannot hide its conflict.
        views = self.knowledge(subject_id, RetrievalScope.PRIVATE, now)
        return tuple(
            view
            for view in views
            if (details := view.record.knowledge) is not None
            and RetrievalScope.PLANNING in view.record.retrieval_scopes
            and details.planning_allowed
            and details.sensitivity is not Sensitivity.RESTRICTED
            and view.current
            and view.state in {KnowledgeState.CONFIRMED, KnowledgeState.OBSERVED}
            and view.record.confidence >= MIN_CONFIDENCE
            and details.kind
            not in {
                KnowledgeKind.OPEN_LOOP,
                KnowledgeKind.QUESTION,
                KnowledgeKind.TRUST_PREFERENCE,
            }
        )
