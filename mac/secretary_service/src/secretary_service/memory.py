"""Typed governed memory records and lifecycle requests."""

from datetime import datetime
from enum import StrEnum
from typing import Protocol, final, override

from pydantic import Field

from secretary_service.models import FrozenModel, NonEmpty, RecordId


class MemoryCategory(StrEnum):
    """Approved factual memory categories; procedures are intentionally absent."""

    PROFILE = "profile"
    PREFERENCE = "preference"
    COMMITMENT = "commitment"
    EPISODE = "episode"
    OBSERVATION = "observation"


class RetrievalScope(StrEnum):
    """Contexts in which a memory may be retrieved."""

    PRIVATE = "private"
    CONVERSATION = "conversation"
    PLANNING = "planning"
    DELEGATION = "delegation"


class MemorySource(StrEnum):
    """Origin classes retained as memory provenance."""

    USER_STATEMENT = "user_statement"
    USER_CORRECTION = "user_correction"
    CONVERSATION = "conversation"
    IMPORT = "import"
    INFERENCE = "inference"


class MemoryRemovalReason(StrEnum):
    """Governed reasons that remove memory content."""

    USER_CORRECTION = "user_correction"
    USER_DELETION = "user_deletion"
    RETENTION_EXPIRED = "retention_expired"


class Clock(Protocol):
    """Clock seam used to enforce memory retention."""

    def now(self) -> datetime:
        """Return the current aware timestamp."""
        ...


class MemoryProvenance(FrozenModel):
    """Traceable source metadata for a memory assertion."""

    source: MemorySource
    source_id: NonEmpty
    captured_at: datetime


class MemoryRecord(FrozenModel):
    """One current governed memory revision."""

    memory_id: RecordId
    category: MemoryCategory
    content: NonEmpty
    provenance: MemoryProvenance
    confidence: float = Field(ge=0.0, le=1.0)
    retrieval_scopes: frozenset[RetrievalScope] = Field(min_length=1)
    created_at: datetime
    retain_until: datetime | None = None
    revision: int = Field(default=1, ge=1)


class MemoryCorrection(FrozenModel):
    """User-authored replacement guarded by an expected revision."""

    expected_revision: int = Field(ge=1)
    content: NonEmpty
    provenance: MemoryProvenance
    confidence: float = Field(ge=0.0, le=1.0)
    retrieval_scopes: frozenset[RetrievalScope] = Field(min_length=1)
    retain_until: datetime | None = None


class MemoryDeletion(FrozenModel):
    """User deletion request guarded by an expected revision."""

    expected_revision: int = Field(ge=1)


class MemoryTombstone(FrozenModel):
    """Content-free evidence for one removed memory revision."""

    memory_id: RecordId
    revision: int
    removed_at: datetime
    reason: MemoryRemovalReason
    source_fingerprint: str


@final
class MemoryNotFoundError(Exception):
    """A requested live memory does not exist."""

    def __init__(self, memory_id: RecordId) -> None:
        """Initialize the missing-memory error."""
        super().__init__(memory_id)
        self.memory_id = memory_id


@final
class StaleMemoryRevisionError(Exception):
    """A correction or deletion targets an obsolete revision."""

    def __init__(self, memory_id: RecordId, expected: int, current: int) -> None:
        """Initialize the stale revision details."""
        super().__init__(memory_id, expected, current)
        self.memory_id = memory_id
        self.expected = expected
        self.current = current

    @override
    def __str__(self) -> str:
        return (
            f"memory {self.memory_id} expected revision {self.expected}, current is {self.current}"
        )
