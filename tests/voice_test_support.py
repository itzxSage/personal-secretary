from dataclasses import dataclass
from typing import Final, final, override
from uuid import UUID

from secretary_service.enrollment import DeviceId
from secretary_service.voice import (
    ProviderSessionConfig,
    ProviderSessionHandle,
    ProviderUnavailableError,
    RealtimeProvider,
    SessionStartRequest,
    VoiceSurface,
)

USER_ID: Final = UUID("11111111-1111-4111-8111-111111111111")
DEVICE_UUID: Final = UUID("33333333-3333-4333-8333-333333333333")
CONVERSATION_ID: Final = UUID("55555555-5555-4555-8555-555555555555")
DEVICE_ID: Final = DeviceId(str(DEVICE_UUID))


def voice_start_request(fingerprint: str) -> SessionStartRequest:
    return SessionStartRequest(
        device_id=DEVICE_ID,
        public_key_fingerprint=fingerprint,
        participant_id=USER_ID,
        conversation_id=CONVERSATION_ID,
        surface=VoiceSurface.PUSH_TO_TALK,
    )


@final
class RecordingRealtimeProvider(RealtimeProvider):
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.started: list[ProviderSessionConfig] = []
        self.audio: list[bytes] = []
        self.cancelled: list[str] = []

    @override
    def start(self, config: ProviderSessionConfig) -> ProviderSessionHandle:
        if not self.available:
            raise ProviderUnavailableError(provider="openai")
        self.started.append(config)
        return ProviderSessionHandle(reference="provider-session-fixture")

    @override
    def send_audio(self, handle: ProviderSessionHandle, pcm: bytes) -> None:
        _ = handle
        self.audio.append(pcm)

    @override
    def cancel_response(self, handle: ProviderSessionHandle) -> None:
        self.cancelled.append(handle.reference)

    @override
    def close(self, handle: ProviderSessionHandle) -> None:
        _ = handle


@dataclass(frozen=True, slots=True)
class RecordingKeychainCustodian:
    rotations: list[tuple[str, str, str]]

    def rotate_database_key(
        self,
        current_reference: str,
        replacement_reference: str,
        recovery_envelope_reference: str,
    ) -> None:
        self.rotations.append(
            (current_reference, replacement_reference, recovery_envelope_reference)
        )
