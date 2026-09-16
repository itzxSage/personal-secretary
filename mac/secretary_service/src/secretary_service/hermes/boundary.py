"""Bounded LifeOS→Hermes capability boundary.

Builds a ``HermesCapabilityRequest`` that the existing loopback Hermes gateway
adapter (``HermesTransport`` / ``HermesHTTPTransport`` at 127.0.0.1:8642) can
send, enforcing three invariants before anything leaves LifeOS:

1. Consent is re-verified on every build (mirrors ``_require_review``).
2. Context text is bounded to ``MAX_INPUT_CHARACTERS`` with a truncation marker.
3. Redactable PII and credential shapes are stripped from the payload.

The boundary composes the ``HermesTransport`` protocol; it never opens its own
HTTP connection, so tests inject a fake gateway with no live loopback.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import uuid4

from pydantic import JsonValue, SecretStr

from secretary_service.capabilities.router import Capability
from secretary_service.hermes.contracts import (
    HERMES_REVISION,
    ConnectorSecrets,
    HermesSettings,
    HermesTransport,
)
from secretary_service.hermes.free_model_adapter import (
    MAX_INPUT_CHARACTERS,
    ErrorResponse,
)
from secretary_service.slice.validation import InterpretationProviderError
from secretary_service.storage import Clock

TRUNCATION_MARKER: Final = "…[truncated]"
REDACTION_PLACEHOLDER: Final = "[REDACTED]"

_REDACTION_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    re.compile(r"\+?\d{1,3}[\s.-]?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}"),
    re.compile(r"\b\d{10,15}\b"),
    re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b"),
    re.compile(r"\b(?:sk|pk)-[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bBearer [A-Za-z0-9._~+/=-]{20,}\b"),
)


class RetentionIntent(StrEnum):
    """Closed retention intents recorded on every capability request."""

    EPHEMERAL = "ephemeral"
    SESSION = "session"
    PERSISTENT = "persistent"


@dataclass(frozen=True)
class Denied:
    """Consent or input failure; no request is built and nothing is sent."""

    reason: str


@dataclass(frozen=True)
class HermesCapabilityRequest:
    """A bounded, redacted request the loopback Hermes gateway can send."""

    origin: str
    capability: Capability
    payload: str
    retention_intent: RetentionIntent

    def to_chat_payload(self, model: str) -> dict[str, JsonValue]:
        """Build the chat-completions payload consumed by the Hermes transport.

        Args:
            model: The Hermes model name selected by deployment settings.

        Returns:
            A bounded chat-completions payload with the redacted context.
        """
        bounded = _bound_text(_redact(self.payload))
        return {
            "model": model,
            "stream": False,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are LifeOS's bounded capability boundary. Reply using only the "
                        "supplied context. You have no tools and no authority to act."
                    ),
                },
                {"role": "user", "content": bounded},
            ],
        }


@dataclass(frozen=True)
class HermesBoundary:
    """Re-verify consent, bound and redact context, then send via the gateway."""

    settings: HermesSettings
    clock: Clock
    secrets: ConnectorSecrets
    transport: HermesTransport

    def _require_review(self) -> None:
        """Require current user consent and an operator-reviewed deployment."""
        review = self.settings.runtime_review()
        if (
            not self.settings.consent_granted()
            or review is None
            or review.revision != HERMES_REVISION
            or review.deployment_id != self.settings.deployment_id
            or not review.evidence_reference.strip()
            or not review.verified_at <= self.clock.now() < review.valid_until
        ):
            message = "Hermes consent or deployment review is not current"
            raise InterpretationProviderError(message)

    def build_request(
        self, origin: str, capability: Capability, context_pack: str
    ) -> HermesCapabilityRequest | Denied:
        """Build a bounded, redacted request or deny it at the boundary.

        Args:
            origin: LifeOS origin of the request (e.g. ``lifeos:relay``).
            capability: The routed capability this request serves.
            context_pack: Raw context text; bounded and redacted before return.

        Returns:
            A bounded request, or ``Denied`` when consent is missing or the
            context pack is empty.
        """
        try:
            self._require_review()
        except InterpretationProviderError as error:
            return Denied(reason=str(error))
        if not context_pack.strip():
            return Denied(reason="context pack is empty")
        return HermesCapabilityRequest(
            origin=origin,
            capability=capability,
            payload=_redact(_bound_text(context_pack)),
            retention_intent=RetentionIntent.EPHEMERAL,
        )

    def send(self, request: HermesCapabilityRequest) -> bytes | ErrorResponse:
        """Send a built request through the existing Hermes gateway transport.

        Args:
            request: A request produced by ``build_request``.

        Returns:
            The raw gateway response body, or an ``ErrorResponse`` when the
            gateway is unavailable.

        Raises:
            InterpretationProviderError: If consent is withdrawn before send.
        """
        self._require_review()
        credential = SecretStr(self.secrets.connector_secret(self.settings.key_reference))
        request_id = uuid4()
        payload = request.to_chat_payload(self.settings.model)
        try:
            return self.transport.request("/v1/chat/completions", payload, credential, request_id)
        except InterpretationProviderError as error:
            return ErrorResponse(status="provider_unavailable", error=str(error))


def _bound_text(text: str) -> str:
    """Truncate text to the hard character cap, keeping the marker inside it.

    Args:
        text: The raw context text.

    Returns:
        Text at most ``MAX_INPUT_CHARACTERS`` long, with the truncation marker
        appended when the input exceeded the cap.
    """
    if len(text) <= MAX_INPUT_CHARACTERS:
        return text
    keep = MAX_INPUT_CHARACTERS - len(TRUNCATION_MARKER)
    return f"{text[:keep]}{TRUNCATION_MARKER}"


def _redact(text: str) -> str:
    """Strip redactable PII and credential shapes from a payload.

    Args:
        text: The bounded context text.

    Returns:
        The text with every matched secret shape replaced by a placeholder.
    """
    redacted = text
    for pattern in _REDACTION_PATTERNS:
        redacted = pattern.sub(REDACTION_PLACEHOLDER, redacted)
    return redacted
