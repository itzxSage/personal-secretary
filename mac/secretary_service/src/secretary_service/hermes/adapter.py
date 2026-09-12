"""Optional Hermes planning interpreter using the existing LifeOS proposal interface."""

import json
from dataclasses import dataclass
from uuid import UUID, uuid4

from pydantic import JsonValue, SecretStr, TypeAdapter, ValidationError

from secretary_service.hermes.contracts import (
    HERMES_REVISION,
    MAX_INPUT_CHARACTERS,
    ApiCapabilities,
    ChatReply,
    ConnectorSecrets,
    HermesSettings,
    HermesTransport,
    Toolsets,
)
from secretary_service.models import RecordId
from secretary_service.planner_models import DayPlanRequest
from secretary_service.slice.models import InterpretationProposal
from secretary_service.slice.validation import (
    InterpretationProviderError,
    preserve_plan_constraints,
)
from secretary_service.storage import Clock


@dataclass(frozen=True)
class HermesInterpretationAdapter:
    """Produce untrusted planning proposals with no state, approval or provider-write APIs."""

    settings: HermesSettings
    clock: Clock
    secrets: ConnectorSecrets
    transport: HermesTransport
    base_plan: DayPlanRequest

    def _require_review(self) -> None:
        review = self.settings.runtime_review()
        now = self.clock.now()
        if (
            not self.settings.consent_granted()
            or review is None
            or review.revision != HERMES_REVISION
            or review.deployment_id != self.settings.deployment_id
            or not review.evidence_reference.strip()
            or not review.verified_at <= now < review.valid_until
        ):
            message = "Hermes consent or deployment review is not current"
            raise InterpretationProviderError(message)

    def interpret(self, text: str, source_event_id: UUID) -> InterpretationProposal:
        """Check revision, consent and tools; validate output against canonical constraints."""
        self._require_review()
        if not text.strip() or len(text) > MAX_INPUT_CHARACTERS or not self.settings.model.strip():
            message = "invalid Hermes interpretation input"
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
            self._require_review()
            payload: dict[str, JsonValue] = {
                "model": self.settings.model,
                "stream": False,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Return only JSON matching the supplied LifeOS day-plan schema. "
                            "Preserve canonical metadata and protected activities. "
                            "Do not execute actions or infer approval from conversation text."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "request": text,
                                "base_plan": self.base_plan.model_dump(mode="json"),
                                "schema": TypeAdapter[JsonValue](JsonValue).validate_python(
                                    DayPlanRequest.model_json_schema()
                                ),
                            }
                        ),
                    },
                ],
            }
            reply = ChatReply.model_validate_json(
                self.transport.request("/v1/chat/completions", payload, credential, request_id)
            )
            if (
                len(reply.choices) != 1
                or reply.choices[0].message.tool_calls
                or reply.choices[0].message.function_call is not None
            ):
                message = "Hermes returned unsupported output"
                raise InterpretationProviderError(message)
            plan = DayPlanRequest.model_validate_json(reply.choices[0].message.content)
        except ValidationError:
            message = "Hermes response failed compatibility or planner validation"
            raise InterpretationProviderError(message) from None
        self._require_review()
        preserve_plan_constraints(self.base_plan, plan)
        return InterpretationProposal(
            proposal_id=RecordId(uuid4()),
            source_event_id=source_event_id,
            provider_version=f"hermes:{HERMES_REVISION}:{self.settings.model}",
            plan_request=plan,
        )
