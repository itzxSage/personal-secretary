from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final
from uuid import UUID

import pytest

from secretary_service.conversation_api import (
    Conversation,
    ConversationDevice,
    ConversationJournal,
    ConversationState,
    DeviceKind,
)
from secretary_service.enrollment import DeviceId, DeviceRegistry, RevokedDeviceError
from secretary_service.keys import DeterministicTestKeyProvider
from secretary_service.models import ActorId
from secretary_service.voice import (
    AudioFrame,
    ConsentRegistry,
    EncryptedTranscriptJournal,
    ProviderRetentionType,
    RelayEndpoint,
    RetentionVerification,
    SessionMode,
    VoiceEventKind,
    VoiceOrderError,
    VoiceServiceDependencies,
    VoiceSessionService,
    VoiceSurface,
    wifi_turn_start_p95_seconds,
)
from tests.helpers import FakeClock
from tests.voice_test_support import (
    CONVERSATION_ID,
    DEVICE_ID,
    DEVICE_UUID,
    USER_ID,
    RecordingRealtimeProvider,
    voice_start_request,
)

NOW: Final = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
TURN_ID: Final = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
FINGERPRINT: Final = "sha256:iphone-fixture"


@dataclass(frozen=True, slots=True)
class VoiceHarness:
    service: VoiceSessionService
    provider: RecordingRealtimeProvider
    transcripts: EncryptedTranscriptJournal
    clock: FakeClock
    devices: DeviceRegistry


def build_harness(tmp_path: Path) -> VoiceHarness:
    clock = FakeClock(NOW)
    registry = DeviceRegistry(clock)
    _ = registry.enroll(DEVICE_ID, FINGERPRINT, ActorId("user"))
    conversation = Conversation(
        conversation_id=CONVERSATION_ID,
        title="Voice fixture",
        participant_ids=[USER_ID],
        device_ids=[DEVICE_UUID],
        created_at=NOW,
        state=ConversationState.ACTIVE,
    )
    device = ConversationDevice(
        device_id=DEVICE_UUID,
        participant_id=USER_ID,
        kind=DeviceKind.IOS,
        name="iPhone",
        created_at=NOW,
    )
    consent = ConsentRegistry()
    consent.grant(VoiceSurface.PUSH_TO_TALK, NOW)
    provider = RecordingRealtimeProvider()
    transcripts = EncryptedTranscriptJournal.open(
        tmp_path / "voice.sqlite",
        DeterministicTestKeyProvider.from_seed(b"voice-session"),
        clock,
    )
    dependencies = VoiceServiceDependencies(
        clock=clock,
        devices=registry,
        conversation_journal=ConversationJournal(conversation, [device]),
        transcripts=transcripts,
        consent=consent,
        provider=provider,
        endpoint=RelayEndpoint(url="wss://life-engine.test/voice"),
        token_signing_key=b"voice-token-signing-key-fixture",
        project_id="project-fixture",
        retention=RetentionVerification(
            project_id="project-fixture",
            retention_type=ProviderRetentionType.ZERO_DATA_RETENTION,
            store=False,
            verified_at=NOW,
            valid_until=NOW + timedelta(days=1),
        ),
    )
    return VoiceHarness(VoiceSessionService(dependencies), provider, transcripts, clock, registry)


def test_partial_then_final_is_ordered_and_audio_is_destroyed(tmp_path: Path) -> None:
    harness = build_harness(tmp_path)
    try:
        started = harness.service.start(voice_start_request(FINGERPRINT))
        frame = AudioFrame(event_id=1, turn_id=TURN_ID, pcm=b"\x01\x00" * 480)

        _ = harness.service.append_audio(started.token.value, frame)
        partial = harness.service.accept_partial(started.token.value, TURN_ID, "move")
        final = harness.service.finalize(started.token.value, TURN_ID, "move workout")

        assert started.mode is SessionMode.PROVIDER_AUDIO
        assert [partial.event_id, final.event_id] == [1, 2]
        assert [partial.kind, final.kind] == [
            VoiceEventKind.TRANSCRIPT_PARTIAL,
            VoiceEventKind.TRANSCRIPT_FINAL,
        ]
        assert harness.service.snapshot(started.token.value).retained_audio_bytes == 0
        assert [entry.text for entry in harness.transcripts.entries()] == ["move workout"]
        assert len(harness.provider.audio) == 1
    finally:
        harness.transcripts.close()


def test_final_without_partial_fails_closed(tmp_path: Path) -> None:
    harness = build_harness(tmp_path)
    try:
        started = harness.service.start(voice_start_request(FINGERPRINT))

        with pytest.raises(VoiceOrderError):
            _ = harness.service.finalize(started.token.value, TURN_ID, "orphan final")
    finally:
        harness.transcripts.close()


def test_barge_in_cancels_tts_and_wins_over_queued_action_progress(tmp_path: Path) -> None:
    harness = build_harness(tmp_path)
    try:
        started = harness.service.start(voice_start_request(FINGERPRINT))
        _ = harness.service.accept_partial(started.token.value, TURN_ID, "stop")
        harness.service.queue_tts(started.token.value, TURN_ID, b"synthetic-tts")
        before = harness.service.queue_action_progress(started.token.value, TURN_ID, "working")

        cancellation = harness.service.barge_in(started.token.value, TURN_ID)
        after = harness.service.queue_action_progress(started.token.value, TURN_ID, "too late")

        snapshot = harness.service.snapshot(started.token.value)
        assert before is not None
        assert cancellation.kind is VoiceEventKind.CANCELLATION
        assert after is None
        assert snapshot.queued_tts_bytes == 0
        assert snapshot.queued_action_progress == 0
        assert harness.provider.cancelled == ["provider-session-fixture"]
    finally:
        harness.transcripts.close()


def test_expired_token_resumes_network_transition_without_duplicate_final(
    tmp_path: Path,
) -> None:
    harness = build_harness(tmp_path)
    try:
        started = harness.service.start(voice_start_request(FINGERPRINT))
        _ = harness.service.accept_partial(started.token.value, TURN_ID, "network")
        original_final = harness.service.finalize(
            started.token.value, TURN_ID, "network transition"
        )
        harness.clock.advance(timedelta(minutes=9, seconds=50))
        harness.service.disconnect(started.token.value)
        harness.clock.advance(timedelta(seconds=20))

        resumed = harness.service.resume(
            started.token.value,
            DEVICE_ID,
            FINGERPRINT,
            last_event_id=1,
        )
        duplicate = harness.service.finalize(resumed.token.value, TURN_ID, "network transition")

        assert resumed.token.value != started.token.value
        assert resumed.replayed_events == (original_final,)
        assert duplicate == original_final
        assert len(harness.transcripts.entries()) == 1
    finally:
        harness.transcripts.close()


def test_revoked_device_token_cannot_reconnect(tmp_path: Path) -> None:
    harness = build_harness(tmp_path)
    try:
        started = harness.service.start(voice_start_request(FINGERPRINT))
        harness.service.disconnect(started.token.value)
        _ = harness.devices.revoke(DeviceId(DEVICE_ID), ActorId("owner"))

        with pytest.raises(RevokedDeviceError):
            _ = harness.service.resume(
                started.token.value,
                DEVICE_ID,
                FINGERPRINT,
                last_event_id=0,
            )
    finally:
        harness.transcripts.close()


def test_wifi_fixture_meets_turn_start_p95_target() -> None:
    latencies = (
        0.72,
        0.78,
        0.82,
        0.86,
        0.91,
        0.96,
        1.01,
        1.04,
        1.08,
        1.12,
        1.16,
        1.18,
        1.21,
        1.22,
        1.25,
        1.29,
        1.31,
        1.39,
        1.47,
        1.62,
    )

    assert wifi_turn_start_p95_seconds(latencies) == 1.47
    assert wifi_turn_start_p95_seconds(latencies) <= 1.5
