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
from secretary_service.enrollment import DeviceRegistry
from secretary_service.keys import DeterministicTestKeyProvider
from secretary_service.models import ActorId
from secretary_service.voice import (
    ConsentRegistry,
    EncryptedTranscriptJournal,
    ProviderRetentionType,
    RelayEndpoint,
    RetentionVerification,
    SessionMode,
    VoiceServiceDependencies,
    VoiceSessionService,
    VoiceSurface,
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
FINGERPRINT: Final = "sha256:privacy-fixture"


@dataclass(frozen=True, slots=True)
class PrivacyHarness:
    service: VoiceSessionService
    provider: RecordingRealtimeProvider
    consent: ConsentRegistry
    transcripts: EncryptedTranscriptJournal


def build_harness(
    tmp_path: Path,
    *,
    consented: bool = True,
    available: bool = True,
    retention_type: ProviderRetentionType = ProviderRetentionType.ZERO_DATA_RETENTION,
    project_id: str = "project-fixture",
) -> PrivacyHarness:
    clock = FakeClock(NOW)
    devices = DeviceRegistry(clock)
    _ = devices.enroll(DEVICE_ID, FINGERPRINT, ActorId("user"))
    conversation = Conversation(
        conversation_id=CONVERSATION_ID,
        title="Privacy fixture",
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
    if consented:
        consent.grant(VoiceSurface.PUSH_TO_TALK, NOW)
    provider = RecordingRealtimeProvider(available=available)
    transcripts = EncryptedTranscriptJournal.open(
        tmp_path / "voice-privacy.sqlite",
        DeterministicTestKeyProvider.from_seed(b"voice-privacy"),
        clock,
    )
    service = VoiceSessionService(
        VoiceServiceDependencies(
            clock=clock,
            devices=devices,
            conversation_journal=ConversationJournal(conversation, [device]),
            transcripts=transcripts,
            consent=consent,
            provider=provider,
            endpoint=RelayEndpoint(url="wss://owned-life-engine.test/voice"),
            token_signing_key=b"separate-token-signing-key",
            project_id="project-fixture",
            retention=RetentionVerification(
                project_id=project_id,
                retention_type=retention_type,
                store=False,
                verified_at=NOW,
                valid_until=NOW + timedelta(hours=12),
            ),
        )
    )
    return PrivacyHarness(service, provider, consent, transcripts)


@pytest.mark.parametrize(
    ("consent_value", "retention_type", "project_id"),
    [
        (False, ProviderRetentionType.ZERO_DATA_RETENTION, "project-fixture"),
        (True, ProviderRetentionType.ORGANIZATION_DEFAULT, "project-fixture"),
        (True, ProviderRetentionType.ZERO_DATA_RETENTION, "foreign-project"),
    ],
)
def test_provider_enablement_fails_closed_without_privacy_preconditions(
    tmp_path: Path,
    consent_value: int,
    retention_type: ProviderRetentionType,
    project_id: str,
) -> None:
    harness = build_harness(
        tmp_path,
        consented=bool(consent_value),
        retention_type=retention_type,
        project_id=project_id,
    )
    try:
        result = harness.service.start(voice_start_request(FINGERPRINT))

        assert result.mode is SessionMode.LOCAL_TEXT
        assert harness.provider.started == []
    finally:
        harness.transcripts.close()


@pytest.mark.parametrize(
    "verification",
    [
        RetentionVerification(
            project_id="project-fixture",
            retention_type=ProviderRetentionType.ZERO_DATA_RETENTION,
            store=True,
            verified_at=NOW,
            valid_until=NOW + timedelta(hours=12),
        ),
        RetentionVerification(
            project_id="project-fixture",
            retention_type=ProviderRetentionType.ZERO_DATA_RETENTION,
            store=False,
            verified_at=NOW - timedelta(hours=12),
            valid_until=NOW,
        ),
    ],
)
def test_retention_verification_fails_closed_for_store_or_stale_evidence(
    verification: RetentionVerification,
) -> None:
    assert not verification.authorizes("project-fixture", NOW)


def test_consent_withdrawal_blocks_new_provider_sessions(tmp_path: Path) -> None:
    first = build_harness(tmp_path)
    try:
        enabled = first.service.start(voice_start_request(FINGERPRINT))
        first.service.end(enabled.token.value)
        first.consent.withdraw(VoiceSurface.PUSH_TO_TALK, NOW + timedelta(seconds=1))

        blocked = first.service.start(voice_start_request(FINGERPRINT))

        assert enabled.mode is SessionMode.PROVIDER_AUDIO
        assert blocked.mode is SessionMode.LOCAL_TEXT
        assert len(first.provider.started) == 1
    finally:
        first.transcripts.close()


def test_provider_outage_uses_local_text_and_redacted_logs(tmp_path: Path) -> None:
    harness = build_harness(tmp_path, available=False)
    try:
        result = harness.service.start(voice_start_request(FINGERPRINT))
        serialized_logs = "\n".join(
            entry.model_dump_json() for entry in harness.service.redacted_logs()
        )

        assert result.mode is SessionMode.LOCAL_TEXT
        assert result.reason == "provider_unavailable"
        assert result.token.value not in serialized_logs
        assert "provider-session" not in serialized_logs
        assert "openai" not in serialized_logs
    finally:
        harness.transcripts.close()


def test_provider_receives_store_false_and_no_keychain_reference(tmp_path: Path) -> None:
    harness = build_harness(tmp_path)
    try:
        result = harness.service.start(voice_start_request(FINGERPRINT))
        config = harness.provider.started[0]
        serialized = config.model_dump_json()

        assert result.mode is SessionMode.PROVIDER_AUDIO
        assert config.store is False
        assert config.sample_rate_hz == 24_000
        assert config.server_vad is True
        assert config.transcript_context == ()
        assert "database-key" not in serialized
        assert "token" not in serialized
    finally:
        harness.transcripts.close()


def test_transcript_journal_is_encrypted_expires_at_180_days_and_has_no_audio_column(
    tmp_path: Path,
) -> None:
    harness = build_harness(tmp_path)
    try:
        started = harness.service.start(voice_start_request(FINGERPRINT))
        turn_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        _ = harness.service.accept_partial(started.token.value, turn_id, "private partial")
        _ = harness.service.finalize(started.token.value, turn_id, "private final")
        entry = harness.transcripts.entries()[0]
        raw_database = harness.transcripts.path.read_bytes()

        assert entry.expires_at == NOW + timedelta(days=180)
        assert "audio" not in harness.transcripts.column_names()
        assert b"private final" not in raw_database
        assert harness.service.snapshot(started.token.value).retained_audio_bytes == 0
    finally:
        harness.transcripts.close()
