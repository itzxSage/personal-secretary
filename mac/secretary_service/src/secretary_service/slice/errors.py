"""Typed errors raised by the tomorrow planning slice."""

from dataclasses import dataclass
from typing import override

from secretary_service.conversation_api import ConversationRejection
from secretary_service.models import RecordId


@dataclass(frozen=True, slots=True)
class CaptureRejectedError(Exception):
    """Conversation ordering or identity rejected a capture event."""

    rejection: ConversationRejection

    @override
    def __str__(self) -> str:
        return f"conversation capture rejected: {self.rejection.value}"


@dataclass(frozen=True, slots=True)
class UnsupportedCaptureError(Exception):
    """A capture contained no final user transcript."""

    event_kind: str

    @override
    def __str__(self) -> str:
        return f"capture event is not a transcript: {self.event_kind}"


@dataclass(frozen=True, slots=True)
class InterpretationNotFoundError(Exception):
    """No schema-validated fixture interpretation matches the text."""

    source_fingerprint: str

    @override
    def __str__(self) -> str:
        return f"no interpretation for capture {self.source_fingerprint}"


@dataclass(frozen=True, slots=True)
class SliceProposalNotFoundError(Exception):
    """A requested in-flight slice proposal does not exist."""

    proposal_id: RecordId

    @override
    def __str__(self) -> str:
        return f"tomorrow proposal {self.proposal_id} not found"


@dataclass(frozen=True, slots=True)
class ProtectedConstraintViolationError(Exception):
    """A replan omitted or changed an established protected interval."""

    activity_id: str

    @override
    def __str__(self) -> str:
        return f"replan changed protected activity {self.activity_id}"
