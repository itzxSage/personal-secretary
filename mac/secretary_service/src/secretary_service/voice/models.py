"""Portable models and invariants for relay-first realtime voice."""

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, ClassVar, Final, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, StringConstraints, model_validator

from secretary_service.enrollment import DeviceId
from secretary_service.models import FrozenModel, NonEmpty

PCM_SAMPLE_RATE_HZ: Final = 24_000
MAX_PCM_FRAME_BYTES: Final = 4_800
SESSION_TOKEN_TTL: Final = timedelta(minutes=10)
RECONNECT_WINDOW: Final = timedelta(seconds=30)
TRANSCRIPT_RETENTION: Final = timedelta(days=180)
ROOT_KEY_ROTATION_INTERVAL: Final = timedelta(days=90)
TURN_START_P95_TARGET_SECONDS: Final = 1.5


class VoiceSurface(StrEnum):
    """Explicit consent scopes matching owned iPhone invocation surfaces."""

    PUSH_TO_TALK = "push_to_talk"
    APP_SHORTCUT = "app_shortcut"
    BACK_TAP = "back_tap"
    ACTION_BUTTON = "action_button"
    CONTROL_CENTER = "control_center"
    LOCK_SCREEN = "lock_screen"
    NOTIFICATION = "notification"


class SessionMode(StrEnum):
    """A session either uses authorized provider audio or local text."""

    PROVIDER_AUDIO = "provider_audio"
    LOCAL_TEXT = "local_text"


class VoiceEventKind(StrEnum):
    """Closed event set on the iPhone-to-relay protocol."""

    TRANSCRIPT_PARTIAL = "transcript_partial"
    TRANSCRIPT_FINAL = "transcript_final"
    ACTION_PROGRESS = "action_progress"
    CANCELLATION = "cancellation"


class ProviderRetentionType(StrEnum):
    """Current project retention-control values relevant to realtime use."""

    ZERO_DATA_RETENTION = "zero_data_retention"
    MODIFIED_ABUSE_MONITORING = "modified_abuse_monitoring"
    ORGANIZATION_DEFAULT = "organization_default"


class RelayEndpoint(FrozenModel):
    """Owned relay endpoint; direct peer/provider transport is not representable."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    url: NonEmpty
    transport: Literal["mtls_websocket"] = "mtls_websocket"
    sample_rate_hz: Literal[24000] = PCM_SAMPLE_RATE_HZ

    @model_validator(mode="after")
    def require_secure_websocket(self) -> "RelayEndpoint":
        """Reject plaintext or direct-provider transport endpoints."""
        if not self.url.startswith("wss://"):
            message = "voice relay endpoint must use wss"
            raise ValueError(message)
        return self


class SessionStartRequest(FrozenModel):
    """mTLS-bound request to start one owned-relay session."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    device_id: DeviceId
    public_key_fingerprint: NonEmpty
    participant_id: UUID
    conversation_id: UUID
    surface: VoiceSurface


class SessionToken(FrozenModel):
    """Opaque ten-minute token bound to one session and one device."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    value: NonEmpty
    session_id: UUID
    device_id: DeviceId
    issued_at: datetime
    expires_at: datetime


class AudioFrame(FrozenModel):
    """One bounded mono PCM16 frame sent only to the owned relay."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    event_id: int = Field(ge=1)
    turn_id: UUID
    pcm: Annotated[bytes, Field(min_length=2, max_length=MAX_PCM_FRAME_BYTES)]
    sample_rate_hz: Literal[24000] = PCM_SAMPLE_RATE_HZ

    @model_validator(mode="after")
    def require_pcm16_alignment(self) -> "AudioFrame":
        """Reject partial PCM16 samples at the relay boundary."""
        if len(self.pcm) % 2 != 0:
            message = "PCM16 frames must contain complete samples"
            raise ValueError(message)
        return self


class ProviderSessionConfig(FrozenModel):
    """Content-minimized server-owned OpenAI Realtime session configuration."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    project_id: NonEmpty
    store: Literal[False] = False
    sample_rate_hz: Literal[24000] = PCM_SAMPLE_RATE_HZ
    input_format: Literal["pcm16"] = "pcm16"
    server_vad: Literal[True] = True
    transcript_context: tuple[
        Annotated[str, StringConstraints(min_length=1, max_length=256)], ...
    ] = ()


class ProviderSessionHandle(FrozenModel):
    """Opaque provider session reference retained only by the Life Engine."""

    reference: NonEmpty


class VoiceEvent(FrozenModel):
    """Monotonically identified realtime event retained for reconnect replay."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    event_id: int = Field(ge=1)
    kind: VoiceEventKind
    turn_id: UUID
    text: str = ""


class SessionStartResult(FrozenModel):
    """Started relay session and its privacy-selected operating mode."""

    token: SessionToken
    mode: SessionMode
    endpoint: RelayEndpoint
    reason: NonEmpty


class ResumeResult(FrozenModel):
    """Replacement token and missed monotonic events after reconnect."""

    token: SessionToken
    replayed_events: tuple[VoiceEvent, ...]


class SessionSnapshot(FrozenModel):
    """Content-free session state used by tests and local diagnostics."""

    retained_audio_bytes: int = Field(ge=0)
    queued_tts_bytes: int = Field(ge=0)
    queued_action_progress: int = Field(ge=0)
    next_event_id: int = Field(ge=1)


class RedactedLogEntry(FrozenModel):
    """Local metadata log that cannot contain token, transcript, or audio payloads."""

    event: NonEmpty
    session_id: UUID
    device_id: DeviceId
    byte_count: int = Field(ge=0)
