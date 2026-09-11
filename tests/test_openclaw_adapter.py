"""OpenClaw out-of-process compatibility boundary contract tests."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

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
    TranscriptPartial,
)
from secretary_service.enrollment import DeviceId, DeviceRegistry
from secretary_service.models import ActorId
from secretary_service.openclaw.adapter import OpenClawAdapter, OpenClawAdapterDependencies
from secretary_service.openclaw.bindings import IdentityBindingRegistry
from secretary_service.openclaw.contracts import (
    ADAPTER_VERSION,
    OPENCLAW_REVISION,
    AdapterCapability,
    AdapterEndpoint,
    BindingEnrollment,
    BoundChannelIdentity,
    ChannelIdentityClaim,
    ChannelSupportState,
    MtlsClientIdentity,
    OpenClawAdapterConfig,
    OpenClawReadRequest,
    OpenClawTextEnvelope,
)
from tests.helpers import FakeClock

START = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
OWNER_ID = DeviceId("iphone-owner")
SERVICE_ID = DeviceId("openclaw-service")
OWNER = MtlsClientIdentity(device_id=OWNER_ID, public_key_fingerprint="owner-fp")
SERVICE = MtlsClientIdentity(device_id=SERVICE_ID, public_key_fingerprint="service-fp")
USER_ID = UUID("11111111-1111-4111-8111-111111111111")
CHANNEL_DEVICE_ID = UUID("22222222-2222-4222-8222-222222222222")
CONVERSATION_ID = UUID("33333333-3333-4333-8333-333333333333")
BINDING_ID = UUID("44444444-4444-4444-8444-444444444444")
TURN_ID = UUID("55555555-5555-4555-8555-555555555555")
EVENT_ID = UUID("66666666-6666-4666-8666-666666666666")


class FakeConversationApi:
    """In-memory implementation of only the typed Conversation API protocol."""

    _conversation: Conversation
    _journal: ConversationJournal
    _events: list[ConversationEvent]
    _available: bool

    def __init__(self, conversation: Conversation, device: ConversationDevice) -> None:
        self._conversation = conversation
        self._journal = ConversationJournal(conversation, [device])
        self._events = []
        self._available = True

    def set_unavailable(self) -> None:
        self._available = False

    def get_conversation(self, conversation_id: UUID) -> Conversation:
        assert conversation_id == self._conversation.conversation_id
        return self._conversation

    def list_events(self, conversation_id: UUID) -> tuple[ConversationEvent, ...]:
        assert conversation_id == self._conversation.conversation_id
        return tuple(self._events)

    def append_event(self, event: ConversationEvent) -> ConversationAcceptance:
        if not self._available:
            raise ConnectionError(event.event_id)
        result = self._journal.accept(event)
        if result.accepted:
            self._events.append(event)
        return result


def make_adapter(
    clock: FakeClock, channel_kind: ChannelKind
) -> tuple[
    OpenClawAdapter,
    FakeConversationApi,
    ChannelIdentityClaim,
    IdentityBindingRegistry,
]:
    devices = DeviceRegistry(clock)
    _ = devices.enroll(OWNER_ID, OWNER.public_key_fingerprint, ActorId("user"))
    _ = devices.enroll(SERVICE_ID, SERVICE.public_key_fingerprint, ActorId("user"))
    bindings = IdentityBindingRegistry(devices=devices, owner_device_id=OWNER_ID)
    external_channel_id = f"{channel_kind.value}-channel"
    external_user_id = f"{channel_kind.value}-user"
    binding = ChannelBinding(
        binding_id=BINDING_ID,
        conversation_id=CONVERSATION_ID,
        channel_kind=channel_kind,
        external_channel_id=external_channel_id,
        external_user_id=external_user_id,
        bound_at=clock.now(),
        state=ChannelBindingState.ACTIVE,
    )
    _ = bindings.enroll(
        BindingEnrollment(
            identity=BoundChannelIdentity(
                binding=binding,
                participant_id=USER_ID,
                device_id=CHANNEL_DEVICE_ID,
                service_account_device_id=SERVICE_ID,
            )
        ),
        OWNER,
    )
    conversation = Conversation(
        conversation_id=CONVERSATION_ID,
        title="OpenClaw text",
        participant_ids=[USER_ID],
        device_ids=[CHANNEL_DEVICE_ID],
        created_at=clock.now(),
        state=ConversationState.ACTIVE,
    )
    channel_device = ConversationDevice(
        device_id=CHANNEL_DEVICE_ID,
        participant_id=USER_ID,
        kind=DeviceKind.CHANNEL,
        name=channel_kind.value,
        created_at=clock.now(),
    )
    api = FakeConversationApi(conversation, channel_device)
    adapter = OpenClawAdapter(
        OpenClawAdapterConfig(service_account_device_id=SERVICE_ID),
        OpenClawAdapterDependencies(api=api, devices=devices, bindings=bindings),
    )
    claim = ChannelIdentityClaim(
        binding_id=BINDING_ID,
        channel_kind=channel_kind,
        external_channel_id=external_channel_id,
        external_user_id=external_user_id,
    )
    return adapter, api, claim, bindings


def make_event(clock: FakeClock) -> ConversationEvent:
    return ConversationEvent(
        event_id=EVENT_ID,
        conversation_id=CONVERSATION_ID,
        participant_id=USER_ID,
        device_id=CHANNEL_DEVICE_ID,
        sequence=1,
        occurred_at=clock.now(),
        kind=ConversationEventKind.TRANSCRIPT_PARTIAL,
        payload=TranscriptPartial(turn_id=TURN_ID, text="hello", is_final=False),
    )


@pytest.mark.parametrize("channel_kind", [ChannelKind.OPENCLAW_WEBCHAT, ChannelKind.TELEGRAM])
def test_initial_text_channels_use_only_typed_conversation_operations(
    clock: FakeClock, channel_kind: ChannelKind
) -> None:
    adapter, api, claim, _ = make_adapter(clock, channel_kind)
    event = make_event(clock)

    result = adapter.append_text(OpenClawTextEnvelope(claim=claim, event=event), SERVICE)

    assert result.accepted
    assert (
        adapter.conversation(OpenClawReadRequest(claim=claim), SERVICE).conversation_id
        == CONVERSATION_ID
    )
    assert adapter.events(OpenClawReadRequest(claim=claim), SERVICE) == (event,)
    assert api.list_events(CONVERSATION_ID) == (event,)


def test_health_pins_versions_and_declares_later_constrained_channels(clock: FakeClock) -> None:
    adapter, _, _, _ = make_adapter(clock, ChannelKind.OPENCLAW_WEBCHAT)

    health = adapter.health(SERVICE)

    assert health.status == "ok"
    assert health.adapter_version == ADAPTER_VERSION
    assert health.openclaw_revision == OPENCLAW_REVISION == "befc0c24"
    support = {item.channel: item.state for item in health.channels}
    assert support[ChannelKind.OPENCLAW_WEBCHAT] == ChannelSupportState.INITIAL_TEXT
    assert support[ChannelKind.TELEGRAM] == ChannelSupportState.INITIAL_TEXT
    assert support[ChannelKind.IMESSAGE] == ChannelSupportState.LATER_CONSTRAINED
    assert support[ChannelKind.EMAIL] == ChannelSupportState.LATER_CONSTRAINED


def test_endpoint_and_capability_allowlists_are_closed(clock: FakeClock) -> None:
    adapter, _, _, _ = make_adapter(clock, ChannelKind.TELEGRAM)

    assert {item.value for item in AdapterEndpoint} == {
        "conversation.get",
        "conversation.events.list",
        "conversation.events.append",
    }
    assert {item.value for item in AdapterCapability} == {
        "conversation.read",
        "conversation.write",
    }
    assert adapter.endpoint_access("life_records.enumerate").allowed is False
    assert adapter.capability_access("database.read").allowed is False
