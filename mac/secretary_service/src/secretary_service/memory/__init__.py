"""Memory package: governed memory records and the LifeMemory protocol.

The legacy ``memory.py`` module was relocated into this package as
``legacy.py`` so the package can re-export its symbols without shadowing
them; existing ``from secretary_service.memory import ...`` imports keep
resolving to the same types.
"""

from secretary_service.memory.base import (
    CanonicalLifeMemoryProvider,
    ConfidenceState,
    LifeMemory,
    MemoryEntry,
    MemoryEntryNotFoundError,
)
from secretary_service.memory.legacy import (
    Clock,
    MemoryCategory,
    MemoryCorrection,
    MemoryDeletion,
    MemoryNotFoundError,
    MemoryProvenance,
    MemoryRecord,
    MemoryRemovalReason,
    MemorySource,
    MemoryTombstone,
    RetrievalScope,
    StaleMemoryRevisionError,
)

__all__ = [
    "CanonicalLifeMemoryProvider",
    "Clock",
    "ConfidenceState",
    "LifeMemory",
    "MemoryCategory",
    "MemoryCorrection",
    "MemoryDeletion",
    "MemoryEntry",
    "MemoryEntryNotFoundError",
    "MemoryNotFoundError",
    "MemoryProvenance",
    "MemoryRecord",
    "MemoryRemovalReason",
    "MemorySource",
    "MemoryTombstone",
    "RetrievalScope",
    "StaleMemoryRevisionError",
]
