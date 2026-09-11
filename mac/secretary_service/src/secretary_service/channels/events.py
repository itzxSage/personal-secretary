"""Deterministic canonical event construction for channel text deliveries."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import UUID, uuid5

from secretary_service.channels.contracts import ChannelTextDelivery
from secretary_service.conversation_api import (
    ConversationEvent,
    ConversationEventKind,
    TranscriptFinal,
    TranscriptPartial,
)
from secretary_service.openclaw.contracts import BoundChannelIdentity

CHANNEL_EVENT_NAMESPACE: Final = UUID("837d61ee-41b9-4c67-a8e8-a376b28cd52d")


class TranscriptStage(StrEnum):
    """Two canonical events emitted for one channel text turn."""

    PARTIAL = "partial"
    FINAL = "final"


@dataclass(frozen=True, slots=True)
class ChannelEventFactory:
    """Derive stable event identities while leaving persistence to Life Engine."""

    identity: BoundChannelIdentity

    def event(
        self,
        delivery: ChannelTextDelivery,
        stage: TranscriptStage,
        sequence: int,
    ) -> ConversationEvent:
        """Build one canonical event at an authoritative sequence position."""
        turn_id = uuid5(CHANNEL_EVENT_NAMESPACE, f"{self.delivery_key(delivery)}:turn")
        match stage:  # noqa: MATCH_OK - basedpyright proves enum exhaustiveness.
            case TranscriptStage.PARTIAL:
                kind = ConversationEventKind.TRANSCRIPT_PARTIAL
                payload = TranscriptPartial(turn_id=turn_id, text=delivery.text)
            case TranscriptStage.FINAL:
                kind = ConversationEventKind.TRANSCRIPT_FINAL
                payload = TranscriptFinal(turn_id=turn_id, text=delivery.text)
        return ConversationEvent(
            event_id=self.event_id(delivery, stage),
            conversation_id=self.identity.binding.conversation_id,
            participant_id=self.identity.participant_id,
            device_id=self.identity.device_id,
            sequence=sequence,
            occurred_at=delivery.sent_at,
            kind=kind,
            payload=payload,
        )

    def event_id(self, delivery: ChannelTextDelivery, stage: TranscriptStage) -> UUID:
        """Derive a stable event ID from external identity and delivery identity."""
        return uuid5(CHANNEL_EVENT_NAMESPACE, f"{self.delivery_key(delivery)}:{stage.value}")

    @staticmethod
    def delivery_key(delivery: ChannelTextDelivery) -> str:
        """Return the collision-resistant source tuple for deterministic IDs."""
        return (
            f"{delivery.channel_kind.value}:{delivery.external_channel_id}:"
            f"{delivery.external_user_id}:{delivery.delivery_id}"
        )
