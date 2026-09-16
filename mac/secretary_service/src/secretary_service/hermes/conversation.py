"""Tool-free Hermes conversation adapter for the LifeOS relay."""

from dataclasses import dataclass
from uuid import uuid4

from pydantic import JsonValue, SecretStr, ValidationError

from secretary_service.conversation_turn import (
    AgentUtterance,
    ConversationTurnReply,
)
from secretary_service.hermes.contracts import (
    HERMES_REVISION,
    ApiCapabilities,
    ChatReply,
    ConnectorSecrets,
    HermesSettings,
    HermesTransport,
    Toolsets,
)
from secretary_service.slice.validation import InterpretationProviderError
from secretary_service.storage import Clock


@dataclass(frozen=True, slots=True)
class HermesConversationAgent:
    """Return advisory speech while rejecting any Hermes tool surface."""

    settings: HermesSettings
    secrets: ConnectorSecrets
    transport: HermesTransport
    clock: Clock

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

    def respond(self, history: tuple[AgentUtterance, ...]) -> ConversationTurnReply:
        """Send bounded history and accept completed text only."""
        self._require_review()
        if not history:
            message = "conversation history is required"
            raise InterpretationProviderError(message)
        credential = SecretStr(self.secrets.connector_secret(self.settings.key_reference))
        request_id = uuid4()
        try:
            _ = ApiCapabilities.model_validate_json(
                self.transport.request("/v1/capabilities", None, credential, request_id)
            )
            toolsets = Toolsets.model_validate_json(
                self.transport.request("/v1/toolsets", None, credential, request_id)
            )
            if any(item.enabled for item in toolsets.data):
                message = "Hermes advisory deployment has enabled tools"
                raise InterpretationProviderError(message)
            messages: list[JsonValue] = [
                {
                    "role": "system",
                    "content": (
                        "You are LifeOS's advisory conversational voice. Reply naturally and "
                        "truthfully using only the supplied conversation. You have no tools, "
                        "no calendar access, and no authority to make changes. Never claim an "
                        "action was performed or that approval was granted. Ask for missing "
                        "planning facts rather than inventing them."
                    ),
                },
                *({"role": item.role, "content": item.content} for item in history),
            ]
            payload: dict[str, JsonValue] = {
                "model": self.settings.model,
                "stream": False,
                "messages": messages,
            }
            reply = ChatReply.model_validate_json(
                self.transport.request("/v1/chat/completions", payload, credential, request_id)
            )
        except ValidationError as error:
            message = "Hermes response failed compatibility validation"
            raise InterpretationProviderError(message) from error
        self._require_review()
        if (
            len(reply.choices) != 1
            or reply.choices[0].message.tool_calls
            or reply.choices[0].message.function_call is not None
            or not reply.choices[0].message.content.strip()
        ):
            message = "Hermes returned unsupported output"
            raise InterpretationProviderError(message)
        return ConversationTurnReply(reply_text=reply.choices[0].message.content.strip())
