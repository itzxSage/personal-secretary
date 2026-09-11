"""Owned-relay voice session state machine."""

import math
from dataclasses import dataclass
from datetime import datetime
from typing import final
from uuid import UUID, uuid4

from secretary_service.conversation_api import ConversationJournal
from secretary_service.enrollment import DeviceId, DeviceRegistry
from secretary_service.storage import Clock
from secretary_service.voice.errors import VoiceAccessError, VoiceOrderError
from secretary_service.voice.journal import EncryptedTranscriptJournal
from secretary_service.voice.models import (
    RECONNECT_WINDOW,
    AudioFrame,
    RedactedLogEntry,
    RelayEndpoint,
    ResumeResult,
    SessionSnapshot,
    SessionStartRequest,
    SessionStartResult,
    VoiceEvent,
)
from secretary_service.voice.privacy import ConsentRegistry, RetentionVerification
from secretary_service.voice.provider import (
    ProviderEnablement,
    RealtimeProvider,
    start_provider_session,
)
from secretary_service.voice.state import ConversationVoiceEmitter, VoiceSessionState
from secretary_service.voice.tokens import SessionTokenManager


@dataclass(frozen=True, slots=True)
class VoiceServiceDependencies:
    """Reusable boundaries required by one conversation-scoped voice service."""

    clock: Clock
    devices: DeviceRegistry
    conversation_journal: ConversationJournal
    transcripts: EncryptedTranscriptJournal
    consent: ConsentRegistry
    provider: RealtimeProvider
    endpoint: RelayEndpoint
    token_signing_key: bytes
    project_id: str
    retention: RetentionVerification


@final
class VoiceSessionService:
    """Coordinate mTLS, consent, provider, ordering, replay, and retention."""

    def __init__(self, dependencies: VoiceServiceDependencies) -> None:
        """Bind all state and security boundaries for one conversation."""
        self._dependencies = dependencies
        self._tokens = SessionTokenManager(dependencies.token_signing_key)
        self._sessions: dict[UUID, VoiceSessionState] = {}
        self._emitter = ConversationVoiceEmitter(
            dependencies.conversation_journal,
            dependencies.clock,
        )
        self._logs: list[RedactedLogEntry] = []

    def start(self, request: SessionStartRequest) -> SessionStartResult:
        """Start one device-bound relay session, locally falling back when required."""
        now = self._dependencies.clock.now()
        _ = self._dependencies.devices.verify_mtls_identity(
            request.device_id,
            request.public_key_fingerprint,
        )
        self._expire_disconnected_sessions(now)
        if any(
            session.request.device_id == request.device_id and not session.ended
            for session in self._sessions.values()
        ):
            raise VoiceAccessError(reason="device already has an active voice session")
        session_id = uuid4()
        mode, handle, reason = start_provider_session(
            ProviderEnablement(
                consent=self._dependencies.consent,
                retention=self._dependencies.retention,
                provider=self._dependencies.provider,
                transcripts=self._dependencies.transcripts,
                project_id=self._dependencies.project_id,
            ),
            request.surface,
            now,
        )
        session = VoiceSessionState(session_id, request, mode, handle)
        self._sessions[session_id] = session
        token = self._tokens.issue(session_id, request.device_id, now)
        self._logs.append(
            RedactedLogEntry(
                event="voice_session_start",
                session_id=session_id,
                device_id=request.device_id,
                byte_count=0,
            )
        )
        return SessionStartResult(
            token=token,
            mode=mode,
            endpoint=self._dependencies.endpoint,
            reason=reason,
        )

    def append_audio(self, token_value: str, frame: AudioFrame) -> bool:
        """Forward one minimum bounded PCM frame without retaining it."""
        session = self._active_session(token_value)
        if frame.event_id <= session.last_client_event_id:
            raise VoiceOrderError(reason="client event ids must increase monotonically")
        session.last_client_event_id = frame.event_id
        handle = session.provider_handle
        if handle is None:
            return False
        self._dependencies.provider.send_audio(handle, frame.pcm)
        self._logs.append(
            RedactedLogEntry(
                event="voice_audio_forward",
                session_id=session.session_id,
                device_id=session.request.device_id,
                byte_count=len(frame.pcm),
            )
        )
        return True

    def accept_partial(self, token_value: str, turn_id: UUID, text: str) -> VoiceEvent:
        """Accept an ordered partial through the canonical Conversation journal."""
        session = self._active_session(token_value)
        if turn_id in session.cancelled_turns or turn_id in session.final_events:
            raise VoiceOrderError(reason="partial received for a closed turn")
        event = self._emitter.partial(session, turn_id, text)
        session.partial_turns.add(turn_id)
        return event

    def finalize(self, token_value: str, turn_id: UUID, text: str) -> VoiceEvent:
        """Persist one final transcript, deduplicating reconnect retries."""
        session = self._active_session(token_value)
        existing = session.final_events.get(turn_id)
        if existing is not None:
            if existing.text != text:
                raise VoiceOrderError(reason="conflicting final transcript")
            return existing
        if turn_id not in session.partial_turns:
            raise VoiceOrderError(reason="final transcript requires a partial")
        event = self._emitter.final(session, turn_id, text)
        _ = self._dependencies.transcripts.append_final(session.session_id, turn_id, text)
        session.final_events[turn_id] = event
        session.partial_turns.remove(turn_id)
        session.queued_tts.clear()
        return event

    def queue_tts(self, token_value: str, turn_id: UUID, pcm: bytes) -> None:
        """Queue transient output audio unless barge-in already cancelled the turn."""
        session = self._active_session(token_value)
        if turn_id not in session.cancelled_turns:
            session.queued_tts.extend(pcm)

    def queue_action_progress(
        self,
        token_value: str,
        turn_id: UUID,
        text: str,
    ) -> VoiceEvent | None:
        """Queue progress only while cancellation has not won the turn."""
        session = self._active_session(token_value)
        if turn_id in session.cancelled_turns:
            return None
        return self._emitter.action_progress(session, turn_id, text)

    def barge_in(self, token_value: str, turn_id: UUID) -> VoiceEvent:
        """Cancel provider output and discard queued TTS/action progress first."""
        session = self._active_session(token_value)
        session.cancelled_turns.add(turn_id)
        session.partial_turns.discard(turn_id)
        session.queued_tts.clear()
        session.action_progress.clear()
        if session.provider_handle is not None:
            self._dependencies.provider.cancel_response(session.provider_handle)
        return self._emitter.cancellation(session, turn_id, "barge_in")

    def disconnect(self, token_value: str) -> None:
        """Open the fixed 30-second reconnect window."""
        session = self._active_session(token_value)
        session.disconnected_at = self._dependencies.clock.now()

    def resume(
        self,
        token_value: str,
        device_id: DeviceId,
        fingerprint: str,
        last_event_id: int,
    ) -> ResumeResult:
        """Renew even an expired token only inside the same-device reconnect window."""
        old_token = self._tokens.verify_for_resume(token_value)
        session = self._sessions[old_token.session_id]
        _ = self._dependencies.devices.verify_mtls_identity(device_id, fingerprint)
        disconnected_at = session.disconnected_at
        now = self._dependencies.clock.now()
        if device_id != old_token.device_id or disconnected_at is None:
            raise VoiceAccessError(reason="voice reconnect identity mismatch")
        if disconnected_at + RECONNECT_WINDOW < now:
            raise VoiceAccessError(reason="voice reconnect window expired")
        replacement = self._tokens.replace(token_value, now)
        session.disconnected_at = None
        return ResumeResult(
            token=replacement,
            replayed_events=tuple(
                event for event in session.events if event.event_id > last_event_id
            ),
        )

    def snapshot(self, token_value: str) -> SessionSnapshot:
        """Return content-free queue and ordering state."""
        session = self._session_for_token(token_value)
        return SessionSnapshot(
            retained_audio_bytes=0,
            queued_tts_bytes=len(session.queued_tts),
            queued_action_progress=len(session.action_progress),
            next_event_id=self._emitter.next_event_id,
        )

    def end(self, token_value: str) -> None:
        """Finalize the relay session and destroy all transient audio."""
        session = self._session_for_token(token_value)
        session.queued_tts.clear()
        session.action_progress.clear()
        if session.provider_handle is not None:
            self._dependencies.provider.close(session.provider_handle)
        session.ended = True
        self._tokens.invalidate(token_value)

    def redacted_logs(self) -> tuple[RedactedLogEntry, ...]:
        """Return local content-free diagnostic metadata."""
        return tuple(self._logs)

    def _active_session(self, token_value: str) -> VoiceSessionState:
        token = self._tokens.verify(token_value, self._dependencies.clock.now())
        session = self._sessions[token.session_id]
        if session.ended or session.disconnected_at is not None:
            raise VoiceAccessError(reason="voice session is not connected")
        return session

    def _session_for_token(self, token_value: str) -> VoiceSessionState:
        token = self._tokens.verify(token_value, self._dependencies.clock.now())
        return self._sessions[token.session_id]

    def _expire_disconnected_sessions(self, now: datetime) -> None:
        for session in self._sessions.values():
            disconnected_at = session.disconnected_at
            if disconnected_at is not None and disconnected_at + RECONNECT_WINDOW < now:
                if session.provider_handle is not None:
                    self._dependencies.provider.close(session.provider_handle)
                session.queued_tts.clear()
                session.action_progress.clear()
                session.ended = True


def wifi_turn_start_p95_seconds(latencies: tuple[float, ...]) -> float:
    """Return the nearest-rank p95 for a deterministic Wi-Fi fixture."""
    if not latencies:
        raise ValueError(latencies)
    ordered = sorted(latencies)
    index = math.ceil(0.95 * len(ordered)) - 1
    return ordered[index]
