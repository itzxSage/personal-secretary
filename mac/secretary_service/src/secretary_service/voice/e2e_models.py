"""Typed fixtures and results for Task 13 verification."""

from datetime import datetime
from typing import ClassVar, final, override
from uuid import UUID

from pydantic import ConfigDict

from secretary_service.authority import Approval, ProposalRecord
from secretary_service.conversation_api import Conversation, ConversationDevice
from secretary_service.models import FrozenModel, NonEmpty
from secretary_service.voice.models import RelayEndpoint


class VoiceE2EGolden(FrozenModel):
    """Exact observable values required for a passing conversation."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    event_ids: tuple[int, ...]
    tts: tuple[NonEmpty, ...]
    transcripts: tuple[NonEmpty, ...]
    approved_replans: int
    execution_leases: int
    cancelled_approval_execution_leases: int
    resume_event_ids: tuple[int, ...]


class RealtimeVoiceFixture(FrozenModel):
    """Owned relay, identity, planning, approval, and golden fixture."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    now: datetime
    conversation: Conversation
    devices: tuple[ConversationDevice, ...]
    fingerprint: NonEmpty
    endpoint: RelayEndpoint
    project_id: NonEmpty
    first_turn_id: UUID
    second_turn_id: UUID
    resume_device_id: UUID
    proposal: ProposalRecord
    approval: Approval
    rest_of_day_reply: NonEmpty
    approved_reply: NonEmpty
    golden: VoiceE2EGolden


class OpenClawVoiceFixture(FrozenModel):
    """Pinned optional-fabric surface admitted by Task 13."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    revision: NonEmpty
    endpoints: tuple[NonEmpty, ...]
    capabilities: tuple[NonEmpty, ...]
    denied_realtime_endpoint: NonEmpty


class VoiceE2EReport(FrozenModel):
    """Machine-readable evidence emitted only after every gate passes."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    status: str
    event_ids: tuple[int, ...]
    tts: tuple[str, ...]
    transcripts: tuple[str, ...]
    approved_replans: int
    execution_leases: int
    cancelled_approval_execution_leases: int
    resume_event_ids: tuple[int, ...]
    channel_bridge_sequences: tuple[int, ...]
    audio_sha256: str
    openclaw_revision: str


@final
class VoiceE2EError(Exception):
    """One deterministic acceptance gate failed."""

    def __init__(self, gate: str) -> None:
        """Record the failed gate without fixture payload content."""
        super().__init__(gate)
        self.gate = gate

    @override
    def __str__(self) -> str:
        return f"voice E2E gate failed: {self.gate}"
