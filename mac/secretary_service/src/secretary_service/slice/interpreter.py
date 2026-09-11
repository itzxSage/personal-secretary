"""Schema-validated interpretation boundary for deterministic fixtures."""

from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol
from uuid import UUID

from secretary_service.slice.errors import InterpretationNotFoundError
from secretary_service.slice.models import InterpretationProposal, InterpretationSeed


class InterpretationAdapter(Protocol):
    """Replaceable structured-output interpretation boundary."""

    def interpret(self, text: str, source_event_id: UUID) -> InterpretationProposal:
        """Return a typed proposal without mutating canonical state."""
        ...


@dataclass(frozen=True, slots=True)
class FixtureInterpretationAdapter:
    """Hermetic mapping of natural text to validated structured output."""

    interpretations: tuple[InterpretationSeed, ...]

    def interpret(self, text: str, source_event_id: UUID) -> InterpretationProposal:
        """Resolve exact fixture text into an interpretation proposal."""
        found = next((item for item in self.interpretations if item.text == text), None)
        if found is None:
            fingerprint = sha256(text.encode()).hexdigest()
            raise InterpretationNotFoundError(source_fingerprint=fingerprint)
        return InterpretationProposal(
            proposal_id=found.proposal_id,
            source_event_id=source_event_id,
            provider_version=found.provider_version,
            plan_request=found.plan_request,
        )
