"""Deterministic capability dispatch; model-assisted classification is a declared boundary."""

from enum import StrEnum
from typing import Protocol, final


class Capability(StrEnum):
    """Closed set of advisory capabilities a spoken turn may route to."""

    WEEK_PLANNING = "week_planning"
    CONVERSATION = "conversation"
    MEMORY = "memory"
    CODING = "coding"


class CapabilityClassifier(Protocol):
    """Boundary for future model-assisted classification (declared, not wired).

    A model-backed implementation may replace the keyword classifier later; the
    router only depends on this protocol so the deterministic default stays
    swappable without touching call sites.
    """

    def classify(self, text: str) -> Capability:
        """Return exactly one capability for a single utterance."""
        ...


@final
class KeywordClassifier:
    """Deterministic keyword rules; the default classifier behind the router.

    WEEK_PLANNING exactly ports the legacy ``is_planning_intent`` behavior: a
    planning verb ("plan", "schedule", "organize") joined to a weekly/calendar
    scope ("week", "calendar"). MEMORY and CODING are conservative keyword
    matches with no legacy parity requirement. Anything else falls through to
    the CONVERSATION default.
    """

    _PLANNING_VERBS = ("plan", "schedule", "organize")
    _WEEKLY_SCOPES = ("week", "calendar")
    _MEMORY_KEYWORDS = ("remember", "remind", "memory", "forget", "recall")
    _CODING_KEYWORDS = ("code", "script", "bug", "function", "refactor", "compile", "program")

    def classify(self, text: str) -> Capability:
        """Return the capability whose deterministic rules match the text."""
        lowered = text.lower()
        planning = any(verb in lowered for verb in self._PLANNING_VERBS)
        weekly = any(scope in lowered for scope in self._WEEKLY_SCOPES)
        if planning and weekly:
            return Capability.WEEK_PLANNING
        if any(keyword in lowered for keyword in self._MEMORY_KEYWORDS):
            return Capability.MEMORY
        if any(keyword in lowered for keyword in self._CODING_KEYWORDS):
            return Capability.CODING
        return Capability.CONVERSATION


@final
class CapabilityRouter:
    """Dispatch a turn to exactly one capability using a bounded classifier."""

    def __init__(self, classifier: CapabilityClassifier | None = None) -> None:
        """Bind the default keyword classifier unless a provider is supplied."""
        self._classifier: CapabilityClassifier = classifier or KeywordClassifier()

    def dispatch(self, text: str | None) -> Capability:
        """Route unknown or empty input to the conversation default."""
        if not text:
            return Capability.CONVERSATION
        return self._classifier.classify(text)
