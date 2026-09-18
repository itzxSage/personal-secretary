"""LifeMemory protocol and the canonical local JSONL provider.

The protocol is deliberately backend-agnostic so a remote or vector store
(OpenViking, Postgres) can implement it later. The canonical provider
persists entries as JSONL in a caller-chosen directory and never touches
secrets, credentials, or a database.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, TypedDict, cast, final, override, runtime_checkable
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

_MIN_K = 1
_TAG_MATCH_WEIGHT = 2
_TEXT_MATCH_WEIGHT = 1
_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


class _ProvenanceJson(TypedDict):
    """JSON shape of a serialized provenance record."""

    source: str
    captured_at: str


class _CorrectionJson(TypedDict):
    """JSON shape of a serialized correction record."""

    text: str
    provenance: _ProvenanceJson
    applied_at: str
    previous_confidence: str
    new_confidence: str


class _EntryJson(TypedDict):
    """JSON shape of a serialized memory entry."""

    id: str
    text: str
    tags: list[str]
    confidence: str
    provenance: _ProvenanceJson
    created_at: str
    updated_at: str
    corrections: list[_CorrectionJson]


class ConfidenceState(StrEnum):
    """Evidence confidence for one memory entry, never an execution permission."""

    CONFIRMED = "confirmed"
    OBSERVED = "observed"
    INFERRED = "inferred"
    STALE = "stale"
    CONFLICTED = "conflicted"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class MemoryProvenance:
    """Traceable origin of one memory entry or correction."""

    source: str
    captured_at: datetime


@dataclass(frozen=True)
class MemoryCorrection:
    """One recorded correction applied to an entry."""

    text: str
    provenance: MemoryProvenance
    applied_at: datetime
    previous_confidence: ConfidenceState
    new_confidence: ConfidenceState


@dataclass(frozen=True)
class MemoryEntry:
    """One memory entry with confidence, provenance, and corrections."""

    id: str
    text: str
    tags: tuple[str, ...]
    confidence: ConfidenceState
    provenance: MemoryProvenance
    created_at: datetime
    updated_at: datetime
    corrections: tuple[MemoryCorrection, ...] = ()

    def to_json(self) -> str:
        """Serialize this entry as one JSONL line."""
        return json.dumps(
            {
                "id": self.id,
                "text": self.text,
                "tags": list(self.tags),
                "confidence": self.confidence.value,
                "provenance": {
                    "source": self.provenance.source,
                    "captured_at": self.provenance.captured_at.isoformat(),
                },
                "created_at": self.created_at.isoformat(),
                "updated_at": self.updated_at.isoformat(),
                "corrections": [
                    {
                        "text": correction.text,
                        "provenance": {
                            "source": correction.provenance.source,
                            "captured_at": correction.provenance.captured_at.isoformat(),
                        },
                        "applied_at": correction.applied_at.isoformat(),
                        "previous_confidence": correction.previous_confidence.value,
                        "new_confidence": correction.new_confidence.value,
                    }
                    for correction in self.corrections
                ],
            },
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, line: str) -> MemoryEntry:
        """Parse one JSONL line into an entry."""
        data = cast("_EntryJson", json.loads(line))
        provenance = MemoryProvenance(
            source=data["provenance"]["source"],
            captured_at=datetime.fromisoformat(data["provenance"]["captured_at"]),
        )
        corrections = tuple(
            MemoryCorrection(
                text=item["text"],
                provenance=MemoryProvenance(
                    source=item["provenance"]["source"],
                    captured_at=datetime.fromisoformat(item["provenance"]["captured_at"]),
                ),
                applied_at=datetime.fromisoformat(item["applied_at"]),
                previous_confidence=ConfidenceState(item["previous_confidence"]),
                new_confidence=ConfidenceState(item["new_confidence"]),
            )
            for item in data["corrections"]
        )
        return cls(
            id=data["id"],
            text=data["text"],
            tags=tuple(data["tags"]),
            confidence=ConfidenceState(data["confidence"]),
            provenance=provenance,
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            corrections=corrections,
        )


@runtime_checkable
class LifeMemory(Protocol):
    """Backend-agnostic long-term memory contract.

    Implementations may persist locally (CanonicalLifeMemoryProvider) or
    delegate to a remote or vector store (OpenViking, Postgres). The protocol
    exposes no secrets, connection strings, or backend assumptions.
    """

    def remember(
        self, entry: str, tags: Sequence[str] = (), *, source: str = "user"
    ) -> MemoryEntry:
        """Store a new memory entry and return it with provenance."""
        ...

    def recall(self, query: str, k: int = 5) -> list[MemoryEntry]:
        """Return up to k entries most relevant to the query."""
        ...

    def search(
        self, query: str = "", *, tags: Sequence[str] | None = None, k: int = 5
    ) -> list[MemoryEntry]:
        """Return entries matching query text and/or tags, most relevant first."""
        ...

    def forget(self, entry_id: str) -> None:
        """Permanently remove an entry by id."""
        ...

    def correct(
        self,
        entry_id: str,
        correction: str,
        *,
        source: str = "user",
        contradicts: bool = False,
    ) -> MemoryEntry:
        """Apply a user correction, updating text and confidence."""
        ...


@final
class MemoryEntryNotFoundError(Exception):
    """A requested memory entry does not exist."""

    def __init__(self, entry_id: str) -> None:
        """Initialize the missing-entry error."""
        super().__init__(entry_id)
        self.entry_id = entry_id


def _tokens(text: str) -> frozenset[str]:
    """Split text into lowercase alphanumeric tokens."""
    return frozenset(_TOKEN_PATTERN.findall(text.casefold()))


@final
class CanonicalLifeMemoryProvider(LifeMemory):
    """JSONL-backed LifeMemory implementation for the canonical local store."""

    def __init__(
        self,
        directory: Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Bind the store directory and an optional deterministic clock."""
        self._directory = directory
        self._path = directory / "life_memory.jsonl"
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)
        directory.mkdir(parents=True, exist_ok=True)
        self._entries: dict[str, MemoryEntry] = {}
        self._load()

    def _load(self) -> None:
        """Load persisted entries from the JSONL store."""
        if not self._path.exists():
            return
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entry = MemoryEntry.from_json(line)
            self._entries[entry.id] = entry

    def _persist(self) -> None:
        """Rewrite the JSONL store with the current entries."""
        _ = self._path.write_text(
            "".join(f"{entry.to_json()}\n" for entry in self._entries.values()),
            encoding="utf-8",
        )

    def _score(self, entry: MemoryEntry, query_tokens: frozenset[str]) -> int:
        """Return a deterministic relevance score for one entry."""
        text_tokens = _tokens(entry.text)
        tag_tokens = _tokens(" ".join(entry.tags))
        return _TAG_MATCH_WEIGHT * len(query_tokens & tag_tokens) + _TEXT_MATCH_WEIGHT * len(
            query_tokens & text_tokens
        )

    @override
    def remember(
        self, entry: str, tags: Sequence[str] = (), *, source: str = "user"
    ) -> MemoryEntry:
        """Store a new memory entry and return it with provenance."""
        now = self._clock()
        memory = MemoryEntry(
            id=str(uuid4()),
            text=entry,
            tags=tuple(tags),
            confidence=ConfidenceState.OBSERVED,
            provenance=MemoryProvenance(source=source, captured_at=now),
            created_at=now,
            updated_at=now,
        )
        self._entries[memory.id] = memory
        self._persist()
        return memory

    @override
    def recall(self, query: str, k: int = 5) -> list[MemoryEntry]:
        """Return up to k entries most relevant to the query."""
        query_tokens = _tokens(query)
        if not query_tokens:
            return []
        if k < _MIN_K:
            message = "k must be at least 1"
            raise ValueError(message)
        ranked = sorted(
            (
                (self._score(entry, query_tokens), entry.updated_at, entry)
                for entry in self._entries.values()
            ),
            key=lambda item: (item[0], item[1]),
            reverse=True,
        )
        return [entry for score, _, entry in ranked if score > 0][:k]

    @override
    def search(
        self,
        query: str = "",
        *,
        tags: Sequence[str] | None = None,
        k: int = 5,
    ) -> list[MemoryEntry]:
        """Return entries matching query text and/or tags, most relevant first."""
        candidates = list(self._entries.values())
        if tags is not None:
            required = frozenset(tags)
            candidates = [entry for entry in candidates if required <= frozenset(entry.tags)]
        query_tokens = _tokens(query)
        if query_tokens:
            ranked = sorted(
                (
                    (self._score(entry, query_tokens), entry.updated_at, entry)
                    for entry in candidates
                ),
                key=lambda item: (item[0], item[1]),
                reverse=True,
            )
            candidates = [entry for score, _, entry in ranked if score > 0]
        else:
            candidates.sort(key=lambda entry: entry.updated_at, reverse=True)
        return candidates[:k]

    @override
    def forget(self, entry_id: str) -> None:
        """Permanently remove an entry by id."""
        if entry_id not in self._entries:
            raise MemoryEntryNotFoundError(entry_id)
        del self._entries[entry_id]
        self._persist()

    @override
    def correct(
        self,
        entry_id: str,
        correction: str,
        *,
        source: str = "user",
        contradicts: bool = False,
    ) -> MemoryEntry:
        """Apply a user correction, updating text and confidence."""
        entry = self._entries.get(entry_id)
        if entry is None:
            raise MemoryEntryNotFoundError(entry_id)
        now = self._clock()
        new_confidence = ConfidenceState.CONFLICTED if contradicts else ConfidenceState.CONFIRMED
        record = MemoryCorrection(
            text=correction,
            provenance=MemoryProvenance(source=source, captured_at=now),
            applied_at=now,
            previous_confidence=entry.confidence,
            new_confidence=new_confidence,
        )
        updated = MemoryEntry(
            id=entry.id,
            text=correction,
            tags=entry.tags,
            confidence=new_confidence,
            provenance=entry.provenance,
            created_at=entry.created_at,
            updated_at=now,
            corrections=(*entry.corrections, record),
        )
        self._entries[entry_id] = updated
        self._persist()
        return updated

    def mark_stale(self, entry_id: str) -> MemoryEntry:
        """Transition an entry to STALE without altering its text."""
        entry = self._entries.get(entry_id)
        if entry is None:
            raise MemoryEntryNotFoundError(entry_id)
        now = self._clock()
        updated = MemoryEntry(
            id=entry.id,
            text=entry.text,
            tags=entry.tags,
            confidence=ConfidenceState.STALE,
            provenance=entry.provenance,
            created_at=entry.created_at,
            updated_at=now,
            corrections=entry.corrections,
        )
        self._entries[entry_id] = updated
        self._persist()
        return updated
