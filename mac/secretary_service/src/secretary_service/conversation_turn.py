"""Authenticated, advisory conversation turns; only deterministic code grants authority."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol, final, override
from uuid import UUID, uuid4

from pydantic import Field, JsonValue

from secretary_service.capabilities import Capability, CapabilityRouter
from secretary_service.models import FrozenModel, TransitionContext
from secretary_service.relay_store import AgentTurnPersistence, ConversationRelayStore
from secretary_service.slice.models import InterpretationProposal
from secretary_service.slice.validation import InterpretationProviderError


class ConversationTurn(FrozenModel):
    """A bounded utterance in an existing LifeOS-owned conversation."""

    conversation_id: UUID
    text: str = Field(min_length=1, max_length=16_000)


class ProposedAction(FrozenModel):
    """Closed advisory intents, never arbitrary capability names or executable payloads."""

    action_class: Literal["week_plan_preview", "approval_required"]
    payload: dict[str, JsonValue] = Field(default_factory=dict, max_length=0)


class ConversationTurnReply(FrozenModel):
    """Text and bounded routing hints; no approval or completion assertion."""

    reply_text: str = Field(min_length=1, max_length=8_000)
    proposed_actions: tuple[ProposedAction, ...] = Field(default=(), max_length=1)


class AgentUtterance(FrozenModel):
    """One private history message transmitted only to the reviewed agent."""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=16_000)


@dataclass(frozen=True, slots=True)
class ConversationTurnProviderError(Exception):
    """Content-free provider failure suitable for HTTP translation."""

    status: Literal["provider_unavailable", "rate_limited", "consent_required"]
    message: str

    @override
    def __str__(self) -> str:
        """Return a bounded diagnostic."""
        return self.message


class ConversationAgent(Protocol):
    """Reason over bounded history; has no canonical store or action credentials."""

    def respond(self, history: tuple[AgentUtterance, ...]) -> ConversationTurnReply:
        """Return a schema-validated advisory response."""
        ...


@final
class FreeModelConversationAgent:
    """Adapt a validated planning interpreter as a bounded advisory conversation agent."""

    def __init__(self, interpret: Callable[[str, UUID], object]) -> None:
        """Bind a provider that has no LifeOS capability credentials."""
        self._interpret: Callable[[str, UUID], object] = interpret

    def respond(self, history: tuple[AgentUtterance, ...]) -> ConversationTurnReply:
        """Return a truthful summary; proposal execution remains a separate user flow."""
        prompt = "\n".join(f"{item.role}: {item.content}" for item in history)
        result = self._interpret(prompt, uuid4())
        if not isinstance(result, InterpretationProposal):
            message = "agent did not produce a usable advisory result"
            raise InterpretationProviderError(message)
        count = len(result.plan_request.activities)
        return ConversationTurnReply(
            reply_text=(
                f"I drafted an advisory plan with {count} activities. "
                "Review a LifeOS proposal before anything changes."
            )
        )


def is_planning_intent(text: str) -> bool:
    """True when a spoken request asks to preview the weekly schedule.

    Matches natural phrasing for week-level calendar planning ("plan my
    week", "schedule my week", "week plan", "organize my calendar"). Requiring
    both a planning verb and a weekly/calendar scope avoids matching bare
    "plan to leave" or unrelated "calendar" references.
    """
    lowered = text.lower()
    planning = "plan" in lowered or "schedule" in lowered or "organize" in lowered
    weekly = "week" in lowered or "calendar" in lowered
    return planning and weekly


_ROUTER = CapabilityRouter()


@final
class ConversationTurnService:
    """Keep continuity in encrypted canonical storage, separate from transport events."""

    def __init__(self, agent: ConversationAgent, conversations: ConversationRelayStore) -> None:
        """Bind the isolated agent and authorized conversation repository."""
        self._agent: ConversationAgent = agent
        self._conversations: ConversationRelayStore = conversations

    async def turn(
        self, turn: ConversationTurn, device_id: UUID, context: TransitionContext
    ) -> ConversationTurnReply:
        """Authorize before inference, avoid blocking the relay, then persist with CAS."""
        revision, stored = self._conversations.agent_history(turn.conversation_id, device_id)
        history: list[AgentUtterance] = []
        for raw in stored:
            prior_turn, prior_reply = raw
            history.extend(
                (
                    AgentUtterance(role="user", content=prior_turn),
                    AgentUtterance(role="assistant", content=prior_reply),
                )
            )
        history.append(AgentUtterance(role="user", content=turn.text))
        if _ROUTER.dispatch(turn.text) is Capability.WEEK_PLANNING:
            # A spoken planning request routes to the deterministic week planner.
            # The client previews via POST /v1/week-plan, so the advisory agent is
            # not invoked (it has no calendar authority or facts to draft from).
            reply = ConversationTurnReply(
                reply_text="I'll preview your week plan from your LifeOS schedule.",
                proposed_actions=(ProposedAction(action_class="week_plan_preview"),),
            )
        else:
            try:
                reply = await asyncio.to_thread(self._agent.respond, tuple(history))
            except InterpretationProviderError as error:
                status = "provider_unavailable"
                message = "agent unavailable or deployment consent not current"
                raise ConversationTurnProviderError(status, message) from error
        self._conversations.save_agent_turn(
            AgentTurnPersistence(
                conversation_id=turn.conversation_id,
                device_id=device_id,
                expected_revision=revision,
                text=turn.text,
                reply=reply.reply_text,
                context=context,
            )
        )
        return reply
