"""Canonical continuity tests for OpenClaw text channel adapters."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import final
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest

from secretary_service.channels.bindings import (
    ChannelBindingConsent,
    ChannelBindingCoordinator,
    ChannelConsentError,
    ChannelConsentRejection,
)
from secretary_service.channels.contracts import ChannelAdapterDependencies, DeliveryStatus
from secretary_service.channels.webchat import WebChatAdapter, WebChatMessage
from secretary_service.conversation_api import (
    ChannelBinding,
    ChannelBindingState,
    ChannelKind,
    Conversation,
    ConversationAcceptance,
    ConversationDevice,
    ConversationEvent,
    ConversationEventKind,
    ConversationJournal,
    ConversationState,
    DeviceKind,
    TranscriptFinal,
    TranscriptPartial,
)
from secretary_service.enrollment import DeviceId, DeviceRegistry
from secretary_service.models import ActorId
from secretary_service.openclaw.adapter import OpenClawAdapter, OpenClawAdapterDependencies
from secretary_service.openclaw.bindings import IdentityBindingRegistry
from secretary_service.openclaw.contracts import (
    BindingEnrollment,
    BoundChannelIdentity,
    MtlsClientIdentity,
    OpenClawAdapterConfig,
)
from tests.helpers import FakeClock

START = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
OWNER_ID = DeviceId("iphone-owner")
SERVICE_ID = DeviceId("openclaw-service")
OWNER = MtlsClientIdentity(device_id=OWNER_ID, public_key_fingerprint="owner-fp")
SERVICE = MtlsClientIdentity(device_id=SERVICE_ID, public_key_fingerprint="service-fp")
USER_ID = UUID("11111111-1111-4111-8111-111111111111")
IPHONE_ID = UUID("22222222-2222-4222-8222-222222222222")
MAC_ID = UUID("33333333-3333-4333-8333-333333333333")
CHANNEL_DEVICE_ID = UUID("44444444-4444-4444-8444-444444444444")
CONVERSATION_ID = UUID("55555555-5555-4555-8555-555555555555")
BINDING_ID = UUID("66666666-6666-4666-8666-666666666666")


@final
class CanonicalConversationApi:
    """In-memory Life Engine conversation journal used at the real adapter seam."""

    def __init__(self, conversation: Conversation, devices: list[ConversationDevice]) -> None:
        self.conversation = conversation
        self.journal = ConversationJournal(conversation, devices)
        self.events: list[ConversationEvent] = []
        self.action_count = 0
        self.available = True

    def get_conversation(self, conversation_id: UUID) -> Conversation:
        if not self.available:
            raise ConnectionError(conversation_id)
        assert conversation_id == self.conversation.conversation_id
        return self.conversation

    def list_events(self, conversation_id: UUID) -> tuple[ConversationEvent, ...]:
        if not self.available:
            raise ConnectionError(conversation_id)
        assert conversation_id == self.conversation.conversation_id
        return tuple(self.events)

    def append_event(self, event: ConversationEvent) -> ConversationAcceptance:
        if not self.available:
            raise ConnectionError(event.event_id)
        result = self.journal.accept(event)
        if result.accepted:
            self.events.append(event)
            if isinstance(event.payload, TranscriptFinal):
                self.action_count += 1
        return result


@dataclass(frozen=True, slots=True)
class ChannelFixture:
    api: CanonicalConversationApi
    dependencies: ChannelAdapterDependencies
    identity: BoundChannelIdentity


def make_channel_fixture(channel_kind: ChannelKind) -> ChannelFixture:
    clock = FakeClock(START)
    devices = DeviceRegistry(clock)
    _ = devices.enroll(OWNER_ID, OWNER.public_key_fingerprint, ActorId("user"))
    _ = devices.enroll(SERVICE_ID, SERVICE.public_key_fingerprint, ActorId("user"))
    bindings = IdentityBindingRegistry(devices, OWNER_ID)
    external_channel_id = "-10042" if channel_kind is ChannelKind.TELEGRAM else "web-session"
    external_user_id = "7007" if channel_kind is ChannelKind.TELEGRAM else "web-user"
    identity = BoundChannelIdentity(
        binding=ChannelBinding(
            binding_id=BINDING_ID,
            conversation_id=CONVERSATION_ID,
            channel_kind=channel_kind,
            external_channel_id=external_channel_id,
            external_user_id=external_user_id,
            bound_at=START,
            state=ChannelBindingState.ACTIVE,
        ),
        participant_id=USER_ID,
        device_id=CHANNEL_DEVICE_ID,
        service_account_device_id=SERVICE_ID,
    )
    _ = ChannelBindingCoordinator(bindings).enroll(
        BindingEnrollment(identity=identity),
        ChannelBindingConsent.from_identity(identity, granted=True),
        OWNER,
    )
    conversation_devices = [
        ConversationDevice(
            device_id=device_id,
            participant_id=USER_ID,
            kind=kind,
            name=name,
            created_at=START,
        )
        for device_id, kind, name in (
            (IPHONE_ID, DeviceKind.IOS, "iPhone"),
            (MAC_ID, DeviceKind.MAC, "MacBook"),
            (CHANNEL_DEVICE_ID, DeviceKind.CHANNEL, channel_kind.value),
        )
    ]
    conversation = Conversation(
        conversation_id=CONVERSATION_ID,
        title="One LifeOS conversation",
        participant_ids=[USER_ID],
        device_ids=[device.device_id for device in conversation_devices],
        created_at=START,
        state=ConversationState.ACTIVE,
    )
    api = CanonicalConversationApi(conversation, conversation_devices)
    openclaw = OpenClawAdapter(
        OpenClawAdapterConfig(service_account_device_id=SERVICE_ID),
        OpenClawAdapterDependencies(api=api, devices=devices, bindings=bindings),
    )
    return ChannelFixture(
        api=api,
        dependencies=ChannelAdapterDependencies(
            openclaw=openclaw,
            client=SERVICE,
            identity=identity,
        ),
        identity=identity,
    )


def append_canonical_turn(
    api: CanonicalConversationApi, device_id: UUID, sequence: int, text: str
) -> None:
    turn_id = uuid5(NAMESPACE_URL, f"turn:{device_id}:{sequence}")
    for offset, payload in enumerate(
        (TranscriptPartial(turn_id=turn_id, text=text), TranscriptFinal(turn_id=turn_id, text=text))
    ):
        kind = (
            ConversationEventKind.TRANSCRIPT_PARTIAL
            if offset == 0
            else ConversationEventKind.TRANSCRIPT_FINAL
        )
        result = api.append_event(
            ConversationEvent(
                event_id=uuid5(NAMESPACE_URL, f"event:{device_id}:{sequence + offset}"),
                conversation_id=CONVERSATION_ID,
                participant_id=USER_ID,
                device_id=device_id,
                sequence=sequence + offset,
                occurred_at=START + timedelta(seconds=sequence + offset),
                kind=kind,
                payload=payload,
            )
        )
        assert result.accepted


def test_voice_desktop_and_webchat_resolve_to_one_conversation() -> None:
    # Given: voice and desktop turns already accepted by the canonical Life Engine journal.
    fixture = make_channel_fixture(ChannelKind.OPENCLAW_WEBCHAT)
    append_canonical_turn(fixture.api, IPHONE_ID, 1, "voice turn")
    append_canonical_turn(fixture.api, MAC_ID, 3, "desktop turn")
    adapter = WebChatAdapter(fixture.dependencies)

    # When: the bound WebChat identity sends the next text turn.
    result = adapter.receive(
        WebChatMessage(
            message_id="web-1",
            conversation_id=fixture.identity.binding.external_channel_id,
            user_id=fixture.identity.binding.external_user_id,
            sent_at=START + timedelta(seconds=5),
            text="channel turn",
        )
    )

    # Then: all devices share one ordered canonical transcript and one action per final.
    synchronized = adapter.synchronize(after_sequence=0)
    assert result.status == DeliveryStatus.ACCEPTED
    assert [event.sequence for event in synchronized.events] == [1, 2, 3, 4, 5, 6]
    assert {event.conversation_id for event in synchronized.events} == {CONVERSATION_ID}
    assert {event.device_id for event in synchronized.events} == {
        IPHONE_ID,
        MAC_ID,
        CHANNEL_DEVICE_ID,
    }
    assert fixture.api.action_count == 3


def test_duplicate_webchat_delivery_creates_no_duplicate_action() -> None:
    # Given: a bound WebChat adapter that accepted one delivery.
    fixture = make_channel_fixture(ChannelKind.OPENCLAW_WEBCHAT)
    adapter = WebChatAdapter(fixture.dependencies)
    message = WebChatMessage(
        message_id="web-dedup",
        conversation_id=fixture.identity.binding.external_channel_id,
        user_id=fixture.identity.binding.external_user_id,
        sent_at=START,
        text="do this once",
    )
    assert adapter.receive(message).status == DeliveryStatus.ACCEPTED

    # When: OpenClaw redelivers the identical message.
    duplicate = adapter.receive(message)

    # Then: canonical dedupe reports a no-op and the final caused only one action.
    assert duplicate.status == DeliveryStatus.DUPLICATE
    assert fixture.api.action_count == 1
    assert len(fixture.api.events) == 2


def test_offline_delivery_flushes_once_and_replays_transcript_on_reconnect() -> None:
    # Given: Life Engine is unavailable when a WebChat message arrives twice.
    fixture = make_channel_fixture(ChannelKind.OPENCLAW_WEBCHAT)
    adapter = WebChatAdapter(fixture.dependencies)
    fixture.api.available = False
    message = WebChatMessage(
        message_id="web-offline",
        conversation_id=fixture.identity.binding.external_channel_id,
        user_id=fixture.identity.binding.external_user_id,
        sent_at=START,
        text="queued once",
    )
    assert adapter.receive(message).status == DeliveryStatus.QUEUED
    assert adapter.receive(message).status == DeliveryStatus.QUEUED

    # When: connectivity returns and the adapter reconnects.
    fixture.api.available = True
    flushed = adapter.reconnect()

    # Then: one turn is canonical, the local raw-delivery outbox is empty, and sync replays it.
    assert [receipt.status for receipt in flushed] == [DeliveryStatus.ACCEPTED]
    assert adapter.pending_count == 0
    assert len(adapter.synchronize(after_sequence=0).events) == 2
    assert fixture.api.action_count == 1


def test_channel_binding_requires_exact_affirmative_consent() -> None:
    # Given: an otherwise valid owner enrollment with consent explicitly declined.
    fixture = make_channel_fixture(ChannelKind.OPENCLAW_WEBCHAT)
    consent = ChannelBindingConsent.from_identity(fixture.identity, granted=False)

    # When: consent is checked before a binding registry operation.
    with pytest.raises(ChannelConsentError) as caught:
        ChannelBindingCoordinator.enforce_consent(fixture.identity, consent)

    # Then: consent fails closed with a typed reason.
    assert caught.value.reason == ChannelConsentRejection.DECLINED
