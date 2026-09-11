"""Owned Life Engine coordination for continuous voice turns."""

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Final, Protocol, final, override
from uuid import UUID

from pydantic import ConfigDict, model_validator

from secretary_service.authority import Approval
from secretary_service.models import FrozenModel, NonEmpty, RecordId
from secretary_service.voice.models import VoiceEvent
from secretary_service.voice.session import VoiceSessionService


class RealtimeToolName(StrEnum):
    """Closed realtime tools implemented only by the owned Life Engine."""

    REST_OF_DAY = "life_engine.plan.rest_of_day"
    REPLAN = "life_engine.plan.replan"


LIFE_ENGINE_REALTIME_TOOL_ALLOWLIST: Final = tuple(RealtimeToolName)


class RealtimeToolRequest(FrozenModel):
    """Typed provider request admitted after the Life Engine allowlist gate."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    tool: RealtimeToolName
    turn_id: UUID
    text: NonEmpty


class LifeEngineToolReply(FrozenModel):
    """Spoken reply and optional consequential-action confirmation."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    spoken_text: NonEmpty
    proposal_id: RecordId | None = None
    confirmation_id: UUID | None = None

    @model_validator(mode="after")
    def require_complete_confirmation(self) -> "LifeEngineToolReply":
        """Keep proposal and confirmation identity inseparable."""
        if (self.proposal_id is None) != (self.confirmation_id is None):
            raise ValueError((self.proposal_id, self.confirmation_id))
        return self


class ApprovedReplan(FrozenModel):
    """Device-approved replan with its one execution lease."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    proposal_id: RecordId
    spoken_text: NonEmpty
    execution_lease_id: RecordId


class VoiceTurnResult(FrozenModel):
    """Observable continuous-turn events and spoken output."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    events: tuple[VoiceEvent, ...]
    spoken_progress: NonEmpty
    spoken_reply: NonEmpty
    confirmation_id: UUID | None = None


class SpokenApprovalOutcome(FrozenModel):
    """Interrupted speech outcome that deliberately carries no lease."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    confirmation_id: UUID
    cancellation_event: VoiceEvent
    spoken_approval_valid: bool
    execution_lease_id: RecordId | None


class CrossDeviceResume(FrozenModel):
    """Missed Life Engine events replayed to another owned device."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    device_id: UUID
    events: tuple[VoiceEvent, ...]


@final
class RealtimeToolAccessError(Exception):
    """A provider requested a tool outside the Life Engine allowlist."""

    def __init__(self, endpoint: str) -> None:
        """Record only the rejected endpoint name."""
        super().__init__(endpoint)
        self.endpoint = endpoint

    @override
    def __str__(self) -> str:
        return f"realtime tool is not allowlisted by Life Engine: {self.endpoint}"


class LifeEngineVoiceTools(Protocol):
    """Owned planning operations reachable from realtime voice."""

    def invoke(self, request: RealtimeToolRequest) -> LifeEngineToolReply:
        """Execute one allowlisted read or proposal operation."""
        ...

    def approve(self, proposal_id: RecordId, approval: Approval) -> ApprovedReplan:
        """Apply a replan only after a separate device-signed approval."""
        ...


class CanonicalChannelBridge(Protocol):
    """Optional adapter that replays canonical Life Engine sequence values."""

    def synchronize(self, after_sequence: int) -> tuple[int, ...]:
        """Return only canonical values newer than the durable cursor."""
        ...


@dataclass(frozen=True, slots=True)
class VoiceTurnInput:
    """Recognized text and allowlisted operation for one continuous turn."""

    turn_id: UUID
    partial_text: str
    final_text: str
    tool: RealtimeToolName


@dataclass(frozen=True, slots=True)
class SpokenApprovalInterruption:
    """Pending confirmation playback interrupted by the user."""

    turn_id: UUID
    confirmation_id: UUID


@dataclass(frozen=True, slots=True)
class _PendingConfirmation:
    proposal_id: RecordId
    turn_id: UUID
    token: str


@final
class VoiceConversationCoordinator:
    """Join voice relay, owned tools, device approval, resume, and channels."""

    def __init__(self, voice: VoiceSessionService, tools: LifeEngineVoiceTools) -> None:
        """Bind the owned relay and Life Engine tool implementation."""
        self._voice = voice
        self._tools = tools
        self._events: list[VoiceEvent] = []
        self._pending: dict[UUID, _PendingConfirmation] = {}
        self._approved: dict[UUID, ApprovedReplan] = {}

    @property
    def approved_replan_count(self) -> int:
        """Return the exactly-once approved replan count."""
        return len(self._approved)

    def endpoint_access(self, endpoint: str) -> RealtimeToolName:
        """Parse a raw provider endpoint through the closed Life Engine allowlist."""
        tool = next(
            (item for item in LIFE_ENGINE_REALTIME_TOOL_ALLOWLIST if item == endpoint), None
        )
        if tool is None:
            raise RealtimeToolAccessError(endpoint)
        return tool

    def turn(
        self,
        token: str,
        request: VoiceTurnInput,
    ) -> VoiceTurnResult:
        """Finalize one user turn and speak progress plus the Life Engine reply."""
        partial = self._voice.accept_partial(token, request.turn_id, request.partial_text)
        final = self._voice.finalize(token, request.turn_id, request.final_text)
        progress_text = self._progress(request.tool)
        progress = self._voice.queue_action_progress(token, request.turn_id, progress_text)
        assert progress is not None  # noqa: S101 - a new turn cannot already be cancelled
        tool_request = RealtimeToolRequest(
            tool=request.tool,
            turn_id=request.turn_id,
            text=request.final_text,
        )
        reply = self._tools.invoke(tool_request)
        self._voice.queue_tts(token, request.turn_id, reply.spoken_text.encode())
        events = (partial, final, progress)
        self._events.extend(events)
        if reply.confirmation_id is not None and reply.proposal_id is not None:
            self._pending[reply.confirmation_id] = _PendingConfirmation(
                proposal_id=reply.proposal_id,
                turn_id=request.turn_id,
                token=token,
            )
        return VoiceTurnResult(
            events=events,
            spoken_progress=progress_text,
            spoken_reply=reply.spoken_text,
            confirmation_id=reply.confirmation_id,
        )

    def interrupt_spoken_approval(
        self,
        token: str,
        interruption: SpokenApprovalInterruption,
    ) -> SpokenApprovalOutcome:
        """Cancel speech without converting it into identity or authority proof."""
        if interruption.confirmation_id not in self._pending:
            raise RealtimeToolAccessError(str(interruption.confirmation_id))
        cancellation = self._voice.barge_in(token, interruption.turn_id)
        self._events.append(cancellation)
        return SpokenApprovalOutcome(
            confirmation_id=interruption.confirmation_id,
            cancellation_event=cancellation,
            spoken_approval_valid=False,
            execution_lease_id=None,
        )

    def approve_on_device(self, confirmation_id: UUID, approval: Approval) -> ApprovedReplan:
        """Apply one pending replan exactly once using device-signed proof."""
        existing = self._approved.get(confirmation_id)
        if existing is not None:
            return existing
        pending = self._pending.get(confirmation_id)
        if pending is None:
            raise RealtimeToolAccessError(str(confirmation_id))
        result = self._tools.approve(pending.proposal_id, approval)
        self._voice.queue_tts(pending.token, pending.turn_id, result.spoken_text.encode())
        self._approved[confirmation_id] = result
        return result

    def resume_on_device(self, device_id: UUID, after_event_id: int) -> CrossDeviceResume:
        """Replay the canonical voice cursor to another owned device."""
        return CrossDeviceResume(
            device_id=device_id,
            events=tuple(event for event in self._events if event.event_id > after_event_id),
        )

    @staticmethod
    def bridge_channel(bridge: CanonicalChannelBridge, after_sequence: int) -> tuple[int, ...]:
        """Synchronize an optional channel strictly through its canonical bridge."""
        return bridge.synchronize(after_sequence)

    @staticmethod
    def _progress(tool: RealtimeToolName) -> str:
        match tool:  # noqa: MATCH_OK - basedpyright proves enum exhaustiveness.
            case RealtimeToolName.REST_OF_DAY:
                return "Checking your Life Plan."
            case RealtimeToolName.REPLAN:
                return "Checking the workout constraints."
