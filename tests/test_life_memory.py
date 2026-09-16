"""Hermetic tests for the LifeMemory protocol and canonical JSONL provider."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from secretary_service.memory import (
    CanonicalLifeMemoryProvider,
    ConfidenceState,
    LifeMemory,
    MemoryEntry,
    MemoryEntryNotFoundError,
    MemoryRecord,
    MemorySource,
    RetrievalScope,
)
from secretary_service.memory.base import MemoryCorrection, MemoryProvenance
from tests.helpers import FakeClock


def _clock() -> FakeClock:
    return FakeClock(datetime(2026, 9, 16, 12, 0, tzinfo=UTC))


def _provider(directory: Path, clock: FakeClock | None = None) -> CanonicalLifeMemoryProvider:
    return CanonicalLifeMemoryProvider(directory, clock=clock.now if clock is not None else None)


def test_provider_satisfies_life_memory_protocol(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    assert isinstance(provider, LifeMemory)


def test_remember_recall_roundtrip(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    entry = provider.remember("I prefer dark mode", tags=["preference"])
    assert isinstance(entry, MemoryEntry)
    assert entry.confidence is ConfidenceState.OBSERVED
    assert entry.provenance.source == "user"
    recalled = provider.recall("dark mode")
    assert [item.id for item in recalled] == [entry.id]
    assert recalled[0].text == "I prefer dark mode"
    assert recalled[0].tags == ("preference",)


def test_recall_empty_store_returns_empty(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    assert provider.recall("anything") == []
    assert provider.search("anything") == []


def test_recall_ranks_by_relevance(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    _ = provider.remember("I prefer dark mode", tags=["preference"])
    _ = provider.remember("Dark mode reduces eye strain", tags=["health"])
    _ = provider.remember("I run on Tuesdays", tags=["routine"])
    recalled = provider.recall("dark mode")
    assert [item.text for item in recalled] == [
        "Dark mode reduces eye strain",
        "I prefer dark mode",
    ]
    assert len(provider.recall("dark mode", k=1)) == 1


def test_search_by_tags(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    _ = provider.remember("I prefer dark mode", tags=["preference", "appearance"])
    _ = provider.remember("I run on Tuesdays", tags=["routine"])
    matches = provider.search("", tags=["preference"])
    assert len(matches) == 1
    assert matches[0].text == "I prefer dark mode"


def test_correction_confirms_and_records_provenance(tmp_path: Path) -> None:
    clock = _clock()
    provider = _provider(tmp_path, clock)
    entry = provider.remember("I prefer dark mode", tags=["preference"])
    clock.advance(timedelta(minutes=1))
    corrected = provider.correct(entry.id, "I prefer light mode", source="user")
    assert corrected.confidence is ConfidenceState.CONFIRMED
    assert corrected.text == "I prefer light mode"
    assert corrected.updated_at > entry.updated_at
    assert len(corrected.corrections) == 1
    correction = corrected.corrections[0]
    assert isinstance(correction, MemoryCorrection)
    assert correction.previous_confidence is ConfidenceState.OBSERVED
    assert correction.new_confidence is ConfidenceState.CONFIRMED
    assert isinstance(correction.provenance, MemoryProvenance)
    assert correction.provenance.source == "user"
    recalled = provider.recall("light mode")
    assert recalled[0].text == "I prefer light mode"


def test_contradictory_correction_marks_conflicted(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    entry = provider.remember("I prefer dark mode", tags=["preference"])
    corrected = provider.correct(entry.id, "I prefer light mode", contradicts=True)
    assert corrected.confidence is ConfidenceState.CONFLICTED
    assert corrected.corrections[0].new_confidence is ConfidenceState.CONFLICTED


def test_mark_stale_transition(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    entry = provider.remember("I prefer dark mode")
    stale = provider.mark_stale(entry.id)
    assert stale.confidence is ConfidenceState.STALE
    assert stale.text == entry.text


def test_forget_removes_entry(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    entry = provider.remember("I prefer dark mode")
    provider.forget(entry.id)
    assert provider.recall("dark mode") == []
    with pytest.raises(MemoryEntryNotFoundError):
        provider.forget(entry.id)


def test_correct_missing_entry_raises(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    with pytest.raises(MemoryEntryNotFoundError):
        _ = provider.correct("missing", "correction")


def test_persistence_across_instances(tmp_path: Path) -> None:
    first = _provider(tmp_path)
    entry = first.remember("I prefer dark mode", tags=["preference"])
    second = _provider(tmp_path)
    recalled = second.recall("dark mode")
    assert [item.id for item in recalled] == [entry.id]
    assert recalled[0].text == "I prefer dark mode"
    assert recalled[0].confidence is ConfidenceState.OBSERVED


def test_correction_persists_across_instances(tmp_path: Path) -> None:
    clock = _clock()
    first = _provider(tmp_path, clock)
    entry = first.remember("I prefer dark mode", tags=["preference"])
    clock.advance(timedelta(minutes=1))
    _ = first.correct(entry.id, "I prefer light mode")
    second = _provider(tmp_path, clock)
    recalled = second.recall("light mode")
    assert recalled[0].text == "I prefer light mode"
    assert recalled[0].confidence is ConfidenceState.CONFIRMED
    assert len(recalled[0].corrections) == 1


def test_legacy_memory_symbols_still_importable() -> None:
    assert MemorySource.USER_STATEMENT.value == "user_statement"
    assert RetrievalScope.PRIVATE.value == "private"
    assert MemoryRecord is not None
