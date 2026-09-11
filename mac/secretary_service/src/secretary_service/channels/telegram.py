"""Authenticated Telegram webhook mapper into the canonical text relay."""

import hmac
from datetime import datetime
from typing import ClassVar, Final, final, override

from pydantic import ConfigDict, Field, SecretStr

from secretary_service.channels.contracts import (
    ChannelAdapterDependencies,
    ChannelTextDelivery,
    DeliveryReceipt,
)
from secretary_service.channels.relay import TextChannelRelay
from secretary_service.conversation_api import ChannelKind
from secretary_service.models import FrozenModel, NonEmpty

TELEGRAM_SECRET_HEADER: Final = "X-Telegram-Bot-Api-Secret-Token"  # noqa: S105


class TelegramChat(FrozenModel):
    """Telegram chat identity needed for exact binding resolution."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    id: int


class TelegramUser(FrozenModel):
    """Telegram sender identity needed for exact binding resolution."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    id: int


class TelegramMessage(FrozenModel):
    """Supported Telegram text message subset parsed at the webhook boundary."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True,
        extra="forbid",
        populate_by_name=True,
    )

    message_id: int
    sent_at: datetime = Field(alias="date")
    chat: TelegramChat
    sender: TelegramUser = Field(alias="from")
    text: NonEmpty


class TelegramUpdate(FrozenModel):
    """Telegram update containing exactly one supported text message."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    update_id: int = Field(ge=0)
    message: TelegramMessage


class TelegramAdapterConfig(FrozenModel):
    """Secret webhook token and caller-persistable Telegram replay cursor."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    webhook_secret: SecretStr
    last_update_id: int | None = Field(default=None, ge=0)


@final
class TelegramAuthenticationError(Exception):
    """Telegram webhook secret verification failed."""

    def __init__(self, update_id: int) -> None:
        """Record only the unauthenticated update identifier."""
        super().__init__(update_id)
        self.update_id = update_id

    @override
    def __str__(self) -> str:
        return f"Telegram update {self.update_id} failed webhook authentication"


@final
class TelegramReplayError(Exception):
    """Telegram update is older than the authenticated replay cursor."""

    def __init__(self, update_id: int, last_update_id: int) -> None:
        """Record the stale update and current cursor without message content."""
        super().__init__(update_id, last_update_id)
        self.update_id = update_id
        self.last_update_id = last_update_id

    @override
    def __str__(self) -> str:
        return f"Telegram update {self.update_id} is behind cursor {self.last_update_id}"


@final
class TelegramAdapter(TextChannelRelay):
    """Authenticate and forward Telegram text through an enrolled binding."""

    def __init__(
        self,
        dependencies: ChannelAdapterDependencies,
        config: TelegramAdapterConfig,
    ) -> None:
        """Bind Telegram authentication and cursor state to one channel identity."""
        super().__init__(dependencies, ChannelKind.TELEGRAM)
        self._config = config
        self._last_update_id = config.last_update_id

    @property
    def last_update_id(self) -> int | None:
        """Return the cursor the caller should persist across adapter restarts."""
        return self._last_update_id

    def receive(
        self,
        update: TelegramUpdate,
        presented_secret: SecretStr,
    ) -> DeliveryReceipt:
        """Verify Telegram authenticity and ordering before forwarding text."""
        authenticated = hmac.compare_digest(
            presented_secret.get_secret_value(),
            self._config.webhook_secret.get_secret_value(),
        )
        if not authenticated:
            raise TelegramAuthenticationError(update.update_id)
        if self._last_update_id is not None and update.update_id < self._last_update_id:
            raise TelegramReplayError(update.update_id, self._last_update_id)
        message = update.message
        receipt = self.receive_delivery(
            ChannelTextDelivery(
                delivery_id=str(update.update_id),
                channel_kind=ChannelKind.TELEGRAM,
                external_channel_id=str(message.chat.id),
                external_user_id=str(message.sender.id),
                sent_at=message.sent_at,
                text=message.text,
            )
        )
        if self._last_update_id is None or update.update_id > self._last_update_id:
            self._last_update_id = update.update_id
        return receipt
