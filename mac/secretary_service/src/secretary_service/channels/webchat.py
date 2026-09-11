"""OpenClaw WebChat mapper into the canonical text relay."""

from datetime import datetime
from typing import ClassVar, final

from pydantic import ConfigDict

from secretary_service.channels.contracts import (
    ChannelAdapterDependencies,
    ChannelTextDelivery,
    DeliveryReceipt,
)
from secretary_service.channels.relay import TextChannelRelay
from secretary_service.conversation_api import ChannelKind
from secretary_service.models import FrozenModel, NonEmpty


class WebChatMessage(FrozenModel):
    """Validated OpenClaw WebChat text boundary."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    message_id: NonEmpty
    conversation_id: NonEmpty
    user_id: NonEmpty
    sent_at: datetime
    text: NonEmpty


@final
class WebChatAdapter(TextChannelRelay):
    """Forward WebChat text through mTLS and exact channel binding checks."""

    def __init__(self, dependencies: ChannelAdapterDependencies) -> None:
        """Bind this mapper to an enrolled OpenClaw WebChat identity."""
        super().__init__(dependencies, ChannelKind.OPENCLAW_WEBCHAT)

    def receive(self, message: WebChatMessage) -> DeliveryReceipt:
        """Normalize one WebChat message and forward or queue it."""
        return self.receive_delivery(
            ChannelTextDelivery(
                delivery_id=message.message_id,
                channel_kind=ChannelKind.OPENCLAW_WEBCHAT,
                external_channel_id=message.conversation_id,
                external_user_id=message.user_id,
                sent_at=message.sent_at,
                text=message.text,
            )
        )
