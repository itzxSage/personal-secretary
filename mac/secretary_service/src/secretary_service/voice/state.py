"""Mutable per-session state and canonical voice event emission."""

from datetime import datetime
from typing import final
from uuid import UUID

from secretary_service.conversation_api import (
    Cancellation,
    ConversationEvent,
    ConversationEventKind,
    ConversationEventPayload,
    ConversationJournal,
    TranscriptFinal,
    TranscriptPartial,
)
from secretary_service.storage import Clock
from secretary_service.voice.errors import VoiceOrderError
from secretary_service.voice.models import (
    ProviderSessionHandle,
    SessionMode,
    SessionStartRequest,
    VoiceEvent,
    VoiceEventKind,
)


@final
class VoiceSessionState:
    """Mutable state confined to one active realtime session."""

    session_id: UUID
    request: SessionStartRequest
    mode: SessionMode
    provider_handle: ProviderSessionHandle | None
    disconnected_at: datetime | None
    ended: bool
    last_client_event_id: int
    queued_tts: bytearray
    action_progress: list[VoiceEvent]
    events: list[VoiceEvent]
    partial_turns: set[UUID]
    cancelled_turns: set[UUID]
    final_events: dict[UUID, VoiceEvent]

    def __init__(
        self,
        session_id: UUID,
        request: SessionStartRequest,
        mode: SessionMode,
        provider_handle: ProviderSessionHandle | None,
    ) -> None:
        """Initialize empty transient queues and turn projections."""
        self.session_id = session_id
        self.request = request
        self.mode = mode
        self.provider_handle = provider_handle
        self.disconnected_at = None
        self.ended = False
        self.last_client_event_id = 0
        self.queued_tts = bytearray()
        self.action_progress = []
        self.events = []
        self.partial_turns = set()
        self.cancelled_turns = set()
        self.final_events = {}


@final
class ConversationVoiceEmitter:
    """Map monotonic relay events through the canonical Conversation journal."""

    def __init__(self, journal: ConversationJournal, clock: Clock) -> None:
        """Bind one conversation journal and initialize monotonic counters."""
        self._journal = journal
        self._clock = clock
        self._next_event_id = 1
        self._next_conversation_sequence = 1

    @property
    def next_event_id(self) -> int:
        """Return the next relay event identifier."""
        return self._next_event_id

    def partial(self, session: VoiceSessionState, turn_id: UUID, text: str) -> VoiceEvent:
        """Emit one canonical partial transcript."""
        event = self._voice(session, VoiceEventKind.TRANSCRIPT_PARTIAL, turn_id, text)
        return self._canonical(session, event, TranscriptPartial(turn_id=turn_id, text=text))

    def final(self, session: VoiceSessionState, turn_id: UUID, text: str) -> VoiceEvent:
        """Emit one canonical final transcript."""
        event = self._voice(session, VoiceEventKind.TRANSCRIPT_FINAL, turn_id, text)
        return self._canonical(session, event, TranscriptFinal(turn_id=turn_id, text=text))

    def cancellation(self, session: VoiceSessionState, turn_id: UUID, reason: str) -> VoiceEvent:
        """Emit one canonical cancellation."""
        event = self._voice(session, VoiceEventKind.CANCELLATION, turn_id, reason)
        return self._canonical(session, event, Cancellation(turn_id=turn_id, reason=reason))

    def action_progress(self, session: VoiceSessionState, turn_id: UUID, text: str) -> VoiceEvent:
        """Emit relay-local action progress for interruptible playback."""
        event = self._voice(session, VoiceEventKind.ACTION_PROGRESS, turn_id, text)
        session.action_progress.append(event)
        return event

    def _voice(
        self,
        session: VoiceSessionState,
        kind: VoiceEventKind,
        turn_id: UUID,
        text: str,
    ) -> VoiceEvent:
        event = VoiceEvent(
            event_id=self._next_event_id,
            kind=kind,
            turn_id=turn_id,
            text=text,
        )
        self._next_event_id += 1
        session.events.append(event)
        return event

    def _canonical(
        self,
        session: VoiceSessionState,
        event: VoiceEvent,
        payload: ConversationEventPayload,
    ) -> VoiceEvent:
        canonical = ConversationEvent(
            event_id=UUID(int=event.event_id),
            conversation_id=session.request.conversation_id,
            participant_id=session.request.participant_id,
            device_id=UUID(str(session.request.device_id)),
            sequence=self._next_conversation_sequence,
            occurred_at=self._clock.now(),
            kind=ConversationEventKind(payload.kind),
            payload=payload,
        )
        acceptance = self._journal.accept(canonical)
        if not acceptance.accepted:
            _ = session.events.pop()
            self._next_event_id -= 1
            reason = (
                "conversation event rejected"
                if acceptance.rejection is None
                else acceptance.rejection.value
            )
            raise VoiceOrderError(reason=reason)
        self._next_conversation_sequence += 1
        return event
