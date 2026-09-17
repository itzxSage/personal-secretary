"""OpenViking-backed LifeMemory provider (AGPL-3.0, pinned 0.4.9).

AGPL-3.0 compliance note:
- License: AGPL-3.0
- Version pin: openviking==0.4.9
- Source: https://github.com/volcengine/OpenViking
- Local-only context backend on 127.0.0.1:1933; does not replace canonical
  LifeOS memory; not distributed.

Each memory entry is stored as a file under ``viking://user/{user}/memories/
lifeos/`` with a JSON frontmatter block (``MemoryEntry.to_json()``) and the
entry text as the body, so the OpenViking server vectorizes the text for
semantic recall. OpenViking has no native confidence-state model, so the
confidence state machine is owned by this abstraction layer and persisted in
the frontmatter.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, TypedDict, TypeVar, cast, final, override
from uuid import uuid4

import httpx
from openviking_sdk.errors import NotFoundError

from secretary_service.memory.base import (
    ConfidenceState,
    LifeMemory,
    MemoryCorrection,
    MemoryEntry,
    MemoryEntryNotFoundError,
    MemoryProvenance,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from openviking_sdk import SyncHTTPClient

_MIN_K = 1
_OVERFETCH_FACTOR = 4
_FRONTMATTER_MARKER = "---"

_T = TypeVar("_T")


class _FindMemoryItem(TypedDict):
    """One memory result returned by the OpenViking ``find`` API."""

    uri: str
    score: float
    abstract: str | None


class _FindResult(TypedDict):
    """JSON shape of the OpenViking ``find`` response."""

    memories: list[_FindMemoryItem]
    total: int


class _LsItem(TypedDict):
    """One file entry returned by the OpenViking ``ls`` API."""

    uri: str


@final
class OpenVikingUnavailableError(Exception):
    """The OpenViking server is unreachable or failed mid-request."""


@final
class OpenVikingLifeMemoryProvider(LifeMemory):
    """OpenViking-backed LifeMemory implementation for the local context backend.

    The provider talks to a local OpenViking server (127.0.0.1:1933) through
    the ``openviking_sdk`` synchronous client. It is additive to the canonical
    JSONL provider and never replaces it.
    """

    def __init__(
        self,
        client: SyncHTTPClient,
        *,
        user: str = "default",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Bind the OpenViking client, user scope, and an optional clock."""
        self._client = client
        self._user = user
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)
        self._root = f"viking://user/{user}/memories/lifeos"
        self._client.initialize()
        self._call(lambda: self._client.mkdir(self._root))

    def _uri(self, entry_id: str) -> str:
        """Return the storage URI for one entry id."""
        return f"{self._root}/{entry_id}.md"

    def _is_lifeos_uri(self, uri: str) -> bool:
        """Return True for entry files directly under the lifeos root."""
        prefix = f"{self._root}/"
        return (
            uri.startswith(prefix)
            and uri.endswith(".md")
            and not uri.endswith(".overview.md")
            and not uri.endswith(".abstract.md")
        )

    def _call(self, operation: Callable[[], _T]) -> _T:
        """Run a client operation, mapping transport failures to a typed error."""
        try:
            return operation()
        except httpx.HTTPError as exc:
            raise OpenVikingUnavailableError(str(exc)) from exc

    def _find(self, query: str, limit: int) -> _FindResult:
        """Run a semantic search and cast the untyped SDK response."""
        return cast("_FindResult", self._client.find(query, limit=limit))

    def _ls(self) -> list[_LsItem]:
        """List the lifeos directory and cast the untyped SDK response."""
        return cast("list[_LsItem]", self._client.ls(self._root))

    def _serialize(self, entry: MemoryEntry) -> str:
        """Serialize an entry as a JSON frontmatter block plus text body."""
        return f"{_FRONTMATTER_MARKER}\n{entry.to_json()}\n{_FRONTMATTER_MARKER}\n{entry.text}\n"

    def _parse(self, content: str) -> MemoryEntry:
        """Parse a stored file back into a MemoryEntry."""
        lines = content.splitlines()
        if not lines or lines[0].strip() != _FRONTMATTER_MARKER:
            message = "malformed OpenViking memory file: missing frontmatter"
            raise ValueError(message)
        json_lines: list[str] = []
        for line in lines[1:]:
            if line.strip() == _FRONTMATTER_MARKER:
                break
            json_lines.append(line)
        if not json_lines:
            message = "malformed OpenViking memory file: empty frontmatter"
            raise ValueError(message)
        return MemoryEntry.from_json("\n".join(json_lines))

    def _entries_from_find(self, result: _FindResult) -> list[MemoryEntry]:
        """Convert ``find`` results into entries, keeping only lifeos files."""
        entries: list[MemoryEntry] = []
        for item in result["memories"]:
            if not self._is_lifeos_uri(item["uri"]):
                continue
            abstract = item["abstract"]
            if abstract is None:
                continue
            entries.append(self._parse(abstract))
        return entries

    def _list_all_entries(self) -> list[MemoryEntry]:
        """List every stored entry by reading the lifeos directory."""
        items = self._call(self._ls)
        entries: list[MemoryEntry] = []
        for item in items:
            if not self._is_lifeos_uri(item["uri"]):
                continue
            content = self._call(lambda uri=item["uri"]: self._client.read(uri))
            entries.append(self._parse(content))
        return entries

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
        _ = self._call(
            lambda: self._client.write(
                self._uri(memory.id), self._serialize(memory), mode="create", wait=True
            )
        )
        return memory

    @override
    def recall(self, query: str, k: int = 5) -> list[MemoryEntry]:
        """Return up to k entries most relevant to the query."""
        if k < _MIN_K:
            message = "k must be at least 1"
            raise ValueError(message)
        result = self._call(lambda: self._find(query, limit=k))
        return self._entries_from_find(result)

    @override
    def search(
        self,
        query: str = "",
        *,
        tags: Sequence[str] | None = None,
        k: int = 5,
    ) -> list[MemoryEntry]:
        """Return entries matching query text and/or tags, most relevant first."""
        if k < _MIN_K:
            message = "k must be at least 1"
            raise ValueError(message)
        if query:
            result = self._call(lambda: self._find(query, limit=max(k * _OVERFETCH_FACTOR, 16)))
            entries = self._entries_from_find(result)
        else:
            entries = self._list_all_entries()
        if tags is not None:
            required = frozenset(tags)
            entries = [entry for entry in entries if required <= frozenset(entry.tags)]
        return entries[:k]

    @override
    def forget(self, entry_id: str) -> None:
        """Permanently remove an entry by id."""
        try:
            _ = self._call(lambda: self._client.read(self._uri(entry_id)))
        except NotFoundError as exc:
            raise MemoryEntryNotFoundError(entry_id) from exc
        self._call(lambda: self._client.rm(self._uri(entry_id)))

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
        try:
            content = self._call(lambda: self._client.read(self._uri(entry_id)))
        except NotFoundError as exc:
            raise MemoryEntryNotFoundError(entry_id) from exc
        entry = self._parse(content)
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
        _ = self._call(
            lambda: self._client.write(
                self._uri(entry_id), self._serialize(updated), mode="replace", wait=True
            )
        )
        return updated
