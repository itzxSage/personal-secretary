"""Explicit live wiring for the reviewed, tool-free Hermes conversation relay."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import ClassVar

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from secretary_service.conversation_turn import ConversationTurnService
from secretary_service.hermes.contracts import HermesRuntimeReview, HermesSettings
from secretary_service.hermes.conversation import HermesConversationAgent
from secretary_service.hermes.transport import HermesHTTPTransport
from secretary_service.keys import KeyProvider
from secretary_service.storage import Clock, EncryptedStateStore


class LiveConversationTurnConfig(BaseModel):
    """Explicit local configuration; secret values must stay in Keychain."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    model: str = Field(min_length=1)
    key_reference: str = Field(min_length=1)
    consent_until: AwareDatetime
    deployment_id: str = Field(min_length=1)
    runtime_review: HermesRuntimeReview
    origin: str = "http://127.0.0.1:8642"


def live_conversation_turn_factory(
    clock: Clock, keys: KeyProvider, config: LiveConversationTurnConfig
) -> Callable[[EncryptedStateStore], ConversationTurnService]:
    """Build the reviewed Hermes boundary without granting execution authority."""

    def factory(store: EncryptedStateStore) -> ConversationTurnService:
        settings = HermesSettings(
            deployment_id=config.deployment_id,
            model=config.model,
            key_reference=config.key_reference,
            consent_granted=lambda: datetime.now(UTC) < config.consent_until,
            runtime_review=lambda: config.runtime_review,
        )
        return ConversationTurnService(
            HermesConversationAgent(
                settings=settings,
                secrets=keys,
                transport=HermesHTTPTransport(origin=config.origin, allow_loopback_http=True),
                clock=clock,
            ),
            store.conversations,
        )

    return factory
