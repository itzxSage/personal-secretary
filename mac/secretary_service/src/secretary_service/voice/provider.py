"""Server-owned realtime provider protocol and privacy-gated startup."""

from dataclasses import dataclass
from datetime import datetime
from typing import Final, Protocol

from secretary_service.voice.errors import ProviderUnavailableError
from secretary_service.voice.journal import EncryptedTranscriptJournal
from secretary_service.voice.models import (
    ProviderSessionConfig,
    ProviderSessionHandle,
    SessionMode,
    VoiceSurface,
)
from secretary_service.voice.privacy import ConsentRegistry, RetentionVerification

LOCAL_REASON: Final = "privacy_gate_closed"
PROVIDER_REASON: Final = "provider_enabled"
OUTAGE_REASON: Final = "provider_unavailable"


class RealtimeProvider(Protocol):
    """Life Engine provider seam; credentials never enter voice protocol models."""

    def start(self, config: ProviderSessionConfig) -> ProviderSessionHandle:
        """Start a provider session after all privacy gates pass."""
        ...

    def send_audio(self, handle: ProviderSessionHandle, pcm: bytes) -> None:
        """Send one already-bounded PCM frame."""
        ...

    def cancel_response(self, handle: ProviderSessionHandle) -> None:
        """Cancel provider output immediately on barge-in."""
        ...

    def close(self, handle: ProviderSessionHandle) -> None:
        """Close the server-owned provider session."""
        ...


@dataclass(frozen=True, slots=True)
class ProviderEnablement:
    """Inputs needed to authorize and start a content-minimized provider session."""

    consent: ConsentRegistry
    retention: RetentionVerification
    provider: RealtimeProvider
    transcripts: EncryptedTranscriptJournal
    project_id: str


def start_provider_session(
    enablement: ProviderEnablement,
    surface: VoiceSurface,
    now: datetime,
) -> tuple[SessionMode, ProviderSessionHandle | None, str]:
    """Start provider audio only after current consent and retention verification."""
    authorized = enablement.consent.is_granted(surface, now) and enablement.retention.authorizes(
        enablement.project_id, now
    )
    if not authorized:
        return SessionMode.LOCAL_TEXT, None, LOCAL_REASON
    config = ProviderSessionConfig(
        project_id=enablement.project_id,
        transcript_context=enablement.transcripts.latest_context(),
    )
    try:
        return SessionMode.PROVIDER_AUDIO, enablement.provider.start(config), PROVIDER_REASON
    except ProviderUnavailableError:
        return SessionMode.LOCAL_TEXT, None, OUTAGE_REASON
