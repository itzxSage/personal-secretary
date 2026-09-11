"""Typed records shared by OpenClaw text channel adapters."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import ClassVar, final, override
from uuid import UUID

from pydantic import ConfigDict

from secretary_service.conversation_api import (
    ChannelKind,
    ConversationEvent,
    ConversationRejection,
)
from secretary_service.models import FrozenModel, NonEmpty
from secretary_service.openclaw.adapter import OpenClawAdapter
from secretary_service.openclaw.contracts import BoundChannelIdentity, MtlsClientIdentity


class DeliveryStatus(StrEnum):
    """Observable state of one external text delivery."""

    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    QUEUED = "queued"
    REJECTED = "rejected"


class ChannelTextDelivery(FrozenModel):
    """Normalized non-canonical text retained only while transport is offline."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    delivery_id: NonEmpty
    channel_kind: ChannelKind
    external_channel_id: NonEmpty
    external_user_id: NonEmpty
    sent_at: datetime
    text: NonEmpty


class DeliveryReceipt(FrozenModel):
    """Result of forwarding one external delivery to the canonical API."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    delivery_id: NonEmpty
    status: DeliveryStatus
    canonical_event_ids: tuple[UUID, ...]
    next_sequence: int | None
    rejection: ConversationRejection | None = None


class ChannelSyncBatch(FrozenModel):
    """Canonical transcript events replayed from Life Engine after a cursor."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    after_sequence: int
    next_sequence: int
    events: tuple[ConversationEvent, ...]


@dataclass(frozen=True, slots=True)
class ChannelAdapterDependencies:
    """OpenClaw boundary and its exact enrolled channel identity."""

    openclaw: OpenClawAdapter
    client: MtlsClientIdentity
    identity: BoundChannelIdentity


@final
class ChannelReplayError(Exception):
    """A delivery identifier was reused with contradictory content or identity."""

    def __init__(self, delivery_id: str) -> None:
        """Record the conflicting external delivery identifier."""
        super().__init__(delivery_id)
        self.delivery_id = delivery_id

    @override
    def __str__(self) -> str:
        return f"channel delivery {self.delivery_id} contradicts its canonical replay"


@final
class ChannelConfigurationError(Exception):
    """Adapter configuration contradicts its enrolled binding."""

    def __init__(self, channel_kind: ChannelKind) -> None:
        """Record the channel kind rejected by configuration validation."""
        super().__init__(channel_kind)
        self.channel_kind = channel_kind

    @override
    def __str__(self) -> str:
        return f"adapter configuration does not match channel {self.channel_kind.value}"


@final
class ChannelCursorError(Exception):
    """A transcript replay cursor is outside the canonical sequence domain."""

    def __init__(self, after_sequence: int) -> None:
        """Record the invalid replay cursor."""
        super().__init__(after_sequence)
        self.after_sequence = after_sequence

    @override
    def __str__(self) -> str:
        return f"channel replay cursor must be non-negative: {self.after_sequence}"
