"""Hermes conversation boundary tests with no live provider or secret."""

import json
from datetime import timedelta
from typing import final
from uuid import UUID

import pytest
from pydantic import JsonValue, SecretStr

from secretary_service.conversation_turn import AgentUtterance
from secretary_service.hermes.contracts import (
    HERMES_REVISION,
    HermesRuntimeReview,
    HermesSettings,
)
from secretary_service.hermes.conversation import HermesConversationAgent
from secretary_service.slice.validation import InterpretationProviderError
from tests.helpers import FakeClock


@final
class RecordingSecrets:
    """Supply only the isolated sidecar credential."""

    def connector_secret(self, reference: str) -> str:
        """Return a synthetic API credential."""
        assert reference == "hermes-api-server-key"
        return "synthetic-sidecar-key"


@final
class RecordingTransport:
    """Represent the exact three allowlisted Hermes endpoints."""

    def __init__(self, *, tool_enabled: bool = False) -> None:
        self.tool_enabled = tool_enabled
        self.chat_payloads: list[dict[str, JsonValue]] = []

    def request(
        self,
        endpoint: str,
        payload: dict[str, JsonValue] | None,
        credential: SecretStr,
        request_id: UUID,
    ) -> bytes:
        """Return compatible discovery and chat responses."""
        assert credential.get_secret_value() == "synthetic-sidecar-key"
        assert request_id.version == 4
        if endpoint == "/v1/capabilities":
            return json.dumps(
                {
                    "object": "hermes.api_server.capabilities",
                    "platform": "hermes-agent",
                    "auth": {"type": "bearer", "required": True},
                    "features": {"chat_completions": True},
                }
            ).encode()
        if endpoint == "/v1/toolsets":
            return json.dumps(
                {
                    "object": "list",
                    "platform": "api_server",
                    "data": [
                        {
                            "name": "api_server",
                            "enabled": self.tool_enabled,
                            "tools": ["terminal"] if self.tool_enabled else [],
                        }
                    ],
                }
            ).encode()
        assert endpoint == "/v1/chat/completions"
        assert payload is not None
        self.chat_payloads.append(payload)
        return json.dumps(
            {
                "object": "chat.completion",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "I remember that context."},
                        "finish_reason": "stop",
                    }
                ],
            }
        ).encode()


def agent(
    clock: FakeClock, transport: RecordingTransport | None = None
) -> tuple[HermesConversationAgent, RecordingTransport]:
    """Create an active reviewed advisory agent."""
    active_transport = transport or RecordingTransport()
    review = HermesRuntimeReview(
        deployment_id="lifeos-local-hermes",
        revision=HERMES_REVISION,
        evidence_reference="tests/test_hermes_conversation.py",
        verified_at=clock.now(),
        valid_until=clock.now() + timedelta(hours=1),
    )
    settings = HermesSettings(
        deployment_id="lifeos-local-hermes",
        model="synthetic:free",
        key_reference="hermes-api-server-key",
        consent_granted=lambda: True,
        runtime_review=lambda: review,
    )
    return (
        HermesConversationAgent(settings, RecordingSecrets(), active_transport, clock),
        active_transport,
    )


def test_conversation_passes_prior_turn_as_context(clock: FakeClock) -> None:
    """A follow-up reaches Hermes with the prior user and assistant turns."""
    subject, transport = agent(clock)
    reply = subject.respond(
        (
            AgentUtterance(role="user", content="What does tomorrow look like?"),
            AgentUtterance(role="assistant", content="Tomorrow has a morning meeting."),
            AgentUtterance(role="user", content="Give me another hour of sleep."),
        )
    )
    assert reply.reply_text == "I remember that context."
    messages = transport.chat_payloads[0]["messages"]
    assert isinstance(messages, list)
    assert messages[1:] == [
        {"role": "user", "content": "What does tomorrow look like?"},
        {"role": "assistant", "content": "Tomorrow has a morning meeting."},
        {"role": "user", "content": "Give me another hour of sleep."},
    ]


def test_enabled_hermes_tool_rejects_conversation_before_chat(clock: FakeClock) -> None:
    """Tool discovery prevents a model tool from becoming an authority path."""
    subject, transport = agent(clock, RecordingTransport(tool_enabled=True))
    with pytest.raises(InterpretationProviderError, match="enabled tools"):
        _ = subject.respond((AgentUtterance(role="user", content="Change my calendar."),))
    assert transport.chat_payloads == []


def test_expired_review_rejects_before_sidecar_credential(clock: FakeClock) -> None:
    """Stale deployment evidence cannot send a conversation turn."""
    subject, transport = agent(clock)
    clock.advance(timedelta(hours=2))
    with pytest.raises(InterpretationProviderError, match="review"):
        _ = subject.respond((AgentUtterance(role="user", content="Hello."),))
    assert transport.chat_payloads == []
