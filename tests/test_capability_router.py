"""Deterministic capability router: legacy parity, defaults, and provider seam."""

import pytest

from secretary_service.capabilities import (
    Capability,
    CapabilityClassifier,
    CapabilityRouter,
    KeywordClassifier,
)
from secretary_service.conversation_turn import is_planning_intent

_PLANNING_CORPUS = [
    "Help me plan my week",
    "Plan my week",
    "Can you schedule my week?",
    "I'd like to organize my calendar",
    "Show me a week plan",
    "plan for next week",
    "organize my calendar",
    "schedule my calendar",
    "plan my calendar",
    "week plan",
    "I want to plan a meeting this week",
    "remember to plan my week",
    "write a script to plan my week",
]

_NON_PLANNING_CORPUS = [
    "What's on my calendar this week",
    "Tell me a joke",
    "Schedule the meeting for tomorrow",
    "I don't plan to travel",
    "What's the weather",
    "plan to leave",
    "calendar",
    "plan",
    "schedule",
    "organize",
    "my calendar",
    "next week",
    "what's the weather this week",
    "remember that I like coffee",
    "fix this bug",
    "write a python script",
]

_PARITY_CORPUS = _PLANNING_CORPUS + _NON_PLANNING_CORPUS


@pytest.mark.parametrize("text", _PARITY_CORPUS)
def test_dispatch_week_planning_parity_with_legacy_intent(text: str) -> None:
    """The router's WEEK_PLANNING decision matches the legacy is_planning_intent."""
    router = CapabilityRouter()
    assert (router.dispatch(text) is Capability.WEEK_PLANNING) is is_planning_intent(text)


@pytest.mark.parametrize(
    "text",
    [
        "remember that I like coffee",
        "remind me to call mom",
        "what do you remember about me",
        "forget that I said that",
        "recall my goals",
    ],
)
def test_memory_keywords_route_to_memory(text: str) -> None:
    """Deterministic memory keywords classify as MEMORY."""
    assert CapabilityRouter().dispatch(text) is Capability.MEMORY


@pytest.mark.parametrize(
    "text",
    [
        "write a python script",
        "fix this bug",
        "refactor this function",
        "help me code",
        "compile the project",
    ],
)
def test_coding_keywords_route_to_coding(text: str) -> None:
    """Deterministic coding keywords classify as CODING."""
    assert CapabilityRouter().dispatch(text) is Capability.CODING


@pytest.mark.parametrize(
    "text",
    [
        "remember to plan my week",
        "write a script to plan my week",
        "remind me to schedule my calendar",
    ],
)
def test_week_planning_takes_precedence_over_memory_and_coding(text: str) -> None:
    """Legacy planning parity wins over later keyword rules."""
    assert CapabilityRouter().dispatch(text) is Capability.WEEK_PLANNING


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "Tell me a joke",
        "What's the weather",
        "hello there",
        "asdf qwerty",
    ],
)
def test_unknown_or_empty_routes_to_conversation(text: str) -> None:
    """Unknown and empty input falls through to the conversation default."""
    assert CapabilityRouter().dispatch(text) is Capability.CONVERSATION


def test_none_routes_to_conversation() -> None:
    """None input is treated as the conversation default, never a crash."""
    assert CapabilityRouter().dispatch(None) is Capability.CONVERSATION


def test_keyword_classifier_satisfies_classifier_protocol() -> None:
    """The default classifier conforms to the declared provider boundary."""
    classifier: CapabilityClassifier = KeywordClassifier()
    assert classifier.classify("plan my week") is Capability.WEEK_PLANNING
    assert classifier.classify("hello") is Capability.CONVERSATION


class _FixedClassifier:
    """Stub classifier proving the provider seam is injectable, not wired."""

    def classify(self, text: str) -> Capability:
        del text
        return Capability.MEMORY


def test_router_accepts_injected_classifier() -> None:
    """A future model-assisted classifier can replace the keyword default."""
    router = CapabilityRouter(_FixedClassifier())
    assert router.dispatch("anything at all") is Capability.MEMORY
