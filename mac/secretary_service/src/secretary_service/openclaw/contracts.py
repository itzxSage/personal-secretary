"""Typed contracts for the isolated OpenClaw adapter process."""

from enum import StrEnum
from typing import ClassVar, Final, Protocol
from uuid import UUID

from pydantic import ConfigDict

from secretary_service.contract_version import CONTRACT_VERSION
from secretary_service.conversation_api import (
    ChannelBinding,
    ChannelKind,
    Conversation,
    ConversationAcceptance,
    ConversationEvent,
)
from secretary_service.enrollment import DeviceId
from secretary_service.models import FrozenModel, NonEmpty

OPENCLAW_REVISION: Final = "befc0c24"
ADAPTER_VERSION: Final = "1.0.0"


class AdapterEndpoint(StrEnum):
    """Only Conversation API operations visible to the adapter process."""

    GET_CONVERSATION = "conversation.get"
    LIST_EVENTS = "conversation.events.list"
    APPEND_EVENT = "conversation.events.append"


class AdapterCapability(StrEnum):
    """Closed capabilities granted to the OpenClaw service account."""

    CONVERSATION_READ = "conversation.read"
    CONVERSATION_WRITE = "conversation.write"


class ChannelSupportState(StrEnum):
    """Compatibility state for each Conversation API channel kind."""

    INITIAL_TEXT = "initial_text"
    LATER_CONSTRAINED = "later_constrained"


class AdapterStatus(StrEnum):
    """Authenticated adapter health state."""

    OK = "ok"


class MtlsClientIdentity(FrozenModel):
    """Identity extracted after TLS certificate-chain verification."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    device_id: DeviceId
    public_key_fingerprint: NonEmpty


class BoundChannelIdentity(FrozenModel):
    """Owner-approved external identity bound to one conversation participant."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    binding: ChannelBinding
    participant_id: UUID
    device_id: UUID
    service_account_device_id: DeviceId


class BindingEnrollment(FrozenModel):
    """Owner-controlled channel binding enrollment request."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    identity: BoundChannelIdentity


class ChannelIdentityClaim(FrozenModel):
    """Untrusted OpenClaw channel identity presented with a request."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    binding_id: UUID
    channel_kind: ChannelKind
    external_channel_id: NonEmpty
    external_user_id: NonEmpty


class OpenClawReadRequest(FrozenModel):
    """Typed request for one bound conversation or its event stream."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    claim: ChannelIdentityClaim


class OpenClawTextEnvelope(FrozenModel):
    """Typed text event received from an enrolled OpenClaw channel identity."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    claim: ChannelIdentityClaim
    event: ConversationEvent


class AccessDecision(FrozenModel):
    """Fail-closed endpoint or capability allowlist decision."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    allowed: bool
    endpoint: str
    reason: NonEmpty


class ChannelCompatibility(FrozenModel):
    """Support level for one OpenClaw channel adapter."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    channel: ChannelKind
    state: ChannelSupportState


class RevisionCompatibility(FrozenModel):
    """Decision for a presented OpenClaw upstream revision."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    compatible: bool
    expected_revision: NonEmpty
    presented_revision: NonEmpty


class AdapterHealth(FrozenModel):
    """Non-sensitive authenticated health and compatibility matrix."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    status: AdapterStatus
    adapter_version: NonEmpty
    conversation_contract_version: NonEmpty
    openclaw_revision: NonEmpty
    channels: tuple[ChannelCompatibility, ...]


class OpenClawAdapterConfig(FrozenModel):
    """Pinned service-account identity for the adapter process."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    service_account_device_id: DeviceId


class ConversationApi(Protocol):
    """Narrow out-of-process client for typed Conversation API operations."""

    def get_conversation(self, conversation_id: UUID) -> Conversation:
        """Read one bound conversation."""
        ...

    def list_events(self, conversation_id: UUID) -> tuple[ConversationEvent, ...]:
        """Read one bound conversation's ordered events."""
        ...

    def append_event(self, event: ConversationEvent) -> ConversationAcceptance:
        """Append one typed event through the canonical ordering gate."""
        ...


CHANNEL_MATRIX: Final = (
    ChannelCompatibility(
        channel=ChannelKind.OPENCLAW_WEBCHAT,
        state=ChannelSupportState.INITIAL_TEXT,
    ),
    ChannelCompatibility(channel=ChannelKind.TELEGRAM, state=ChannelSupportState.INITIAL_TEXT),
    ChannelCompatibility(
        channel=ChannelKind.IMESSAGE,
        state=ChannelSupportState.LATER_CONSTRAINED,
    ),
    ChannelCompatibility(
        channel=ChannelKind.EMAIL,
        state=ChannelSupportState.LATER_CONSTRAINED,
    ),
)
INITIAL_TEXT_CHANNELS: Final = frozenset(
    item.channel for item in CHANNEL_MATRIX if item.state == ChannelSupportState.INITIAL_TEXT
)
ALLOWED_ENDPOINTS: Final = tuple(AdapterEndpoint)
ALLOWED_CAPABILITIES: Final = tuple(AdapterCapability)


def adapter_health() -> AdapterHealth:
    """Return the pinned health/version matrix without secret material."""
    return AdapterHealth(
        status=AdapterStatus.OK,
        adapter_version=ADAPTER_VERSION,
        conversation_contract_version=CONTRACT_VERSION,
        openclaw_revision=OPENCLAW_REVISION,
        channels=CHANNEL_MATRIX,
    )


def assess_revision(presented_revision: str) -> RevisionCompatibility:
    """Accept only the reviewed OpenClaw revision."""
    return RevisionCompatibility(
        compatible=presented_revision == OPENCLAW_REVISION,
        expected_revision=OPENCLAW_REVISION,
        presented_revision=presented_revision,
    )
