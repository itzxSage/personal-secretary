"""Telegram webhook authentication, replay, and binding tests."""

from datetime import timedelta

import pytest
from pydantic import SecretStr

from secretary_service.channels.contracts import ChannelReplayError, DeliveryStatus
from secretary_service.channels.telegram import (
    TelegramAdapter,
    TelegramAdapterConfig,
    TelegramAuthenticationError,
    TelegramChat,
    TelegramMessage,
    TelegramReplayError,
    TelegramUpdate,
    TelegramUser,
)
from secretary_service.conversation_api import ChannelKind
from secretary_service.openclaw.bindings import BindingRejection, BindingViolationError
from tests.test_channel_continuity import START, make_channel_fixture

WEBHOOK_SECRET = SecretStr("telegram-webhook-secret")


def make_update(
    update_id: int,
    chat_id: str,
    user_id: str,
    text: str = "telegram turn",
) -> TelegramUpdate:
    return TelegramUpdate(
        update_id=update_id,
        message=TelegramMessage.model_validate(
            {
                "message_id": update_id,
                "date": START + timedelta(seconds=update_id),
                "chat": TelegramChat(id=int(chat_id)),
                "from": TelegramUser(id=int(user_id)),
                "text": text,
            }
        ),
    )


def make_telegram_adapter() -> tuple[TelegramAdapter, str, str]:
    fixture = make_channel_fixture(ChannelKind.TELEGRAM)
    adapter = TelegramAdapter(
        fixture.dependencies,
        TelegramAdapterConfig(webhook_secret=WEBHOOK_SECRET),
    )
    return (
        adapter,
        fixture.identity.binding.external_channel_id,
        fixture.identity.binding.external_user_id,
    )


def test_exact_telegram_redelivery_creates_no_duplicate_action() -> None:
    # Given: an authenticated update already accepted by Life Engine.
    fixture = make_channel_fixture(ChannelKind.TELEGRAM)
    adapter = TelegramAdapter(
        fixture.dependencies,
        TelegramAdapterConfig(webhook_secret=WEBHOOK_SECRET),
    )
    update = make_update(
        10,
        fixture.identity.binding.external_channel_id,
        fixture.identity.binding.external_user_id,
    )
    assert adapter.receive(update, WEBHOOK_SECRET).status == DeliveryStatus.ACCEPTED

    # When: Telegram redelivers the byte-equivalent update.
    replay = adapter.receive(update, WEBHOOK_SECRET)

    # Then: it is an idempotent duplicate and creates no second canonical action.
    assert replay.status == DeliveryStatus.DUPLICATE
    assert fixture.api.action_count == 1
    assert len(fixture.api.events) == 2


def test_tampered_telegram_webhook_replay_fails() -> None:
    # Given: a canonical update ID already bound to accepted transcript content.
    adapter, chat_id, user_id = make_telegram_adapter()
    original = make_update(20, chat_id, user_id)
    assert adapter.receive(original, WEBHOOK_SECRET).status == DeliveryStatus.ACCEPTED
    tampered = original.model_copy(
        update={"message": original.message.model_copy(update={"text": "tampered replay"})}
    )

    # When: the same update ID is replayed with altered content.
    with pytest.raises(ChannelReplayError):
        _ = adapter.receive(tampered, WEBHOOK_SECRET)


def test_stale_telegram_update_id_fails_replay_window() -> None:
    # Given: update 31 advanced the authenticated adapter cursor.
    adapter, chat_id, user_id = make_telegram_adapter()
    _ = adapter.receive(make_update(31, chat_id, user_id), WEBHOOK_SECRET)

    # When: a previously unseen but older update arrives.
    with pytest.raises(TelegramReplayError) as caught:
        _ = adapter.receive(make_update(30, chat_id, user_id), WEBHOOK_SECRET)

    # Then: the stale provider ordering is rejected before creating an action.
    assert caught.value.update_id == 30
    assert caught.value.last_update_id == 31


def test_fake_telegram_webhook_secret_fails_before_life_engine_access() -> None:
    # Given: a Telegram adapter with a configured secret token.
    fixture = make_channel_fixture(ChannelKind.TELEGRAM)
    adapter = TelegramAdapter(
        fixture.dependencies,
        TelegramAdapterConfig(webhook_secret=WEBHOOK_SECRET),
    )
    update = make_update(
        40,
        fixture.identity.binding.external_channel_id,
        fixture.identity.binding.external_user_id,
    )

    # When: a fake webhook presents the wrong secret.
    with pytest.raises(TelegramAuthenticationError):
        _ = adapter.receive(update, SecretStr("forged-secret"))

    # Then: no canonical transcript or action was created.
    assert fixture.api.events == []
    assert fixture.api.action_count == 0


def test_foreign_telegram_binding_fails() -> None:
    # Given: a valid webhook secret but an update claiming a foreign Telegram user.
    fixture = make_channel_fixture(ChannelKind.TELEGRAM)
    adapter = TelegramAdapter(
        fixture.dependencies,
        TelegramAdapterConfig(webhook_secret=WEBHOOK_SECRET),
    )
    foreign = make_update(
        50,
        fixture.identity.binding.external_channel_id,
        "9009",
    )

    # When: the foreign identity tries to use the owner's binding.
    with pytest.raises(BindingViolationError) as caught:
        _ = adapter.receive(foreign, WEBHOOK_SECRET)

    # Then: the existing OpenClaw binding boundary rejects it without canonical writes.
    assert caught.value.reason == BindingRejection.IDENTITY_MISMATCH
    assert fixture.api.events == []
