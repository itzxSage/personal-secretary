"""Hermes capability boundary tests with a fake gateway and no live loopback."""

import json
from datetime import timedelta
from typing import final
from uuid import UUID

from pydantic import JsonValue, SecretStr

from secretary_service.capabilities.router import Capability
from secretary_service.hermes.boundary import (
    REDACTION_PLACEHOLDER,
    TRUNCATION_MARKER,
    Denied,
    HermesBoundary,
    HermesCapabilityRequest,
    RetentionIntent,
)
from secretary_service.hermes.contracts import (
    HERMES_REVISION,
    HermesRuntimeReview,
    HermesSettings,
)
from secretary_service.hermes.free_model_adapter import (
    MAX_INPUT_CHARACTERS,
    ErrorResponse,
)
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
class FakeGateway:
    """In-process fake for the loopback Hermes gateway (127.0.0.1:8642)."""

    def __init__(self) -> None:
        """Create an empty gateway recording chat payloads."""
        self.chat_payloads: list[dict[str, JsonValue]] = []

    def request(
        self,
        endpoint: str,
        payload: dict[str, JsonValue] | None,
        credential: SecretStr,
        request_id: UUID,
    ) -> bytes:
        """Record one chat-completions request and return a bounded reply."""
        assert credential.get_secret_value() == "synthetic-sidecar-key"
        assert request_id.version == 4
        assert endpoint == "/v1/chat/completions"
        assert payload is not None
        self.chat_payloads.append(payload)
        return json.dumps(
            {
                "object": "chat.completion",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "bounded reply"},
                        "finish_reason": "stop",
                    }
                ],
            }
        ).encode()


@final
class DownGateway:
    """A gateway that is unreachable, mirroring a downed loopback sidecar."""

    def request(
        self,
        endpoint: str,
        payload: dict[str, JsonValue] | None,
        credential: SecretStr,
        request_id: UUID,
    ) -> bytes:
        """Raise the transport failure the real adapter would surface."""
        del endpoint, payload, credential, request_id
        message = "Hermes transport unavailable"
        raise InterpretationProviderError(message)


def make_boundary(
    clock: FakeClock, gateway: FakeGateway | None = None
) -> tuple[HermesBoundary, FakeGateway]:
    """Create an active reviewed boundary over a fake gateway."""
    active_gateway = gateway or FakeGateway()
    review = HermesRuntimeReview(
        deployment_id="lifeos-local-hermes",
        revision=HERMES_REVISION,
        evidence_reference="tests/test_hermes_boundary.py",
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
        HermesBoundary(settings, clock, RecordingSecrets(), active_gateway),
        active_gateway,
    )


def test_consent_missing_denies_request(clock: FakeClock) -> None:
    """Withdrawn consent returns a denied result before any gateway call."""
    review = HermesRuntimeReview(
        deployment_id="lifeos-local-hermes",
        revision=HERMES_REVISION,
        evidence_reference="tests/test_hermes_boundary.py",
        verified_at=clock.now(),
        valid_until=clock.now() + timedelta(hours=1),
    )
    settings = HermesSettings(
        deployment_id="lifeos-local-hermes",
        model="synthetic:free",
        key_reference="hermes-api-server-key",
        consent_granted=lambda: False,
        runtime_review=lambda: review,
    )
    gateway = FakeGateway()
    subject = HermesBoundary(settings, clock, RecordingSecrets(), gateway)
    result = subject.build_request("lifeos:relay", Capability.CONVERSATION, "Hello")
    assert isinstance(result, Denied)
    assert "consent" in result.reason
    assert gateway.chat_payloads == []


def test_expired_review_denies_request(clock: FakeClock) -> None:
    """A stale deployment review denies the request before any gateway call."""
    subject, gateway = make_boundary(clock)
    clock.advance(timedelta(hours=2))
    result = subject.build_request("lifeos:relay", Capability.CONVERSATION, "Hello")
    assert isinstance(result, Denied)
    assert "review" in result.reason
    assert gateway.chat_payloads == []


def test_empty_context_denied(clock: FakeClock) -> None:
    """An empty context pack is denied rather than sent."""
    subject, _ = make_boundary(clock)
    result = subject.build_request("lifeos:relay", Capability.CONVERSATION, "   ")
    assert isinstance(result, Denied)
    assert "empty" in result.reason


def test_oversize_context_truncated_with_marker(clock: FakeClock) -> None:
    """Context beyond the cap is truncated to exactly the cap with a marker."""
    subject, _ = make_boundary(clock)
    oversized = "x" * (MAX_INPUT_CHARACTERS + 1000)
    result = subject.build_request("lifeos:relay", Capability.CONVERSATION, oversized)
    assert isinstance(result, HermesCapabilityRequest)
    assert len(result.payload) == MAX_INPUT_CHARACTERS
    assert result.payload.endswith(TRUNCATION_MARKER)


def test_redactable_pii_stripped(clock: FakeClock) -> None:
    """Email, phone and key-shaped strings never reach the payload."""
    subject, _ = make_boundary(clock)
    context = (
        "Contact alice@example.com or +1 (555) 123-4567. "
        "Key sk-abcdefghijklmnopqrstuvwxyz123456 and "
        "token Bearer abcdefghijklmnopqrstuvwxyz1234567890."
    )
    result = subject.build_request("lifeos:relay", Capability.CONVERSATION, context)
    assert isinstance(result, HermesCapabilityRequest)
    assert "alice@example.com" not in result.payload
    assert "+1 (555) 123-4567" not in result.payload
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in result.payload
    assert "Bearer abcdefghijklmnopqrstuvwxyz1234567890" not in result.payload
    assert result.payload.count(REDACTION_PLACEHOLDER) == 4


def test_happy_path_builds_bounded_request(clock: FakeClock) -> None:
    """A valid context builds a request with origin, capability and retention."""
    subject, _ = make_boundary(clock)
    result = subject.build_request("lifeos:relay", Capability.MEMORY, "Remember the 3pm meeting.")
    assert isinstance(result, HermesCapabilityRequest)
    assert result.origin == "lifeos:relay"
    assert result.capability is Capability.MEMORY
    assert result.payload == "Remember the 3pm meeting."
    assert result.retention_intent is RetentionIntent.EPHEMERAL


def test_send_routes_built_request_through_fake_gateway(clock: FakeClock) -> None:
    """The boundary composes the existing gateway transport for sending."""
    subject, gateway = make_boundary(clock)
    context = "Plan tomorrow with alice@example.com and sk-abcdefghijklmnopqrstuvwxyz123456."
    request = subject.build_request("lifeos:relay", Capability.WEEK_PLANNING, context)
    assert isinstance(request, HermesCapabilityRequest)
    response = subject.send(request)
    assert isinstance(response, bytes)
    assert b"bounded reply" in response
    assert len(gateway.chat_payloads) == 1
    payload = gateway.chat_payloads[0]
    assert payload["model"] == "synthetic:free"
    messages = payload["messages"]
    assert isinstance(messages, list)
    user_message = messages[1]
    assert isinstance(user_message, dict)
    content = user_message["content"]
    assert isinstance(content, str)
    assert "alice@example.com" not in content
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in content
    assert REDACTION_PLACEHOLDER in content


def test_gateway_down_returns_error_response(clock: FakeClock) -> None:
    """An unreachable gateway yields a graceful error response, not a raise."""
    review = HermesRuntimeReview(
        deployment_id="lifeos-local-hermes",
        revision=HERMES_REVISION,
        evidence_reference="tests/test_hermes_boundary.py",
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
    subject = HermesBoundary(settings, clock, RecordingSecrets(), DownGateway())
    request = subject.build_request("lifeos:relay", Capability.CONVERSATION, "Hello")
    assert isinstance(request, HermesCapabilityRequest)
    result = subject.send(request)
    assert isinstance(result, ErrorResponse)
    assert result.status == "provider_unavailable"
