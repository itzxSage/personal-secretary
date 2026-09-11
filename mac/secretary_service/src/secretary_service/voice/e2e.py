"""Hermetic Task 13 voice-conversation verification driver."""

import hashlib
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Final

from secretary_service.authority import default_approval_matrix
from secretary_service.conversation_api import ConversationJournal
from secretary_service.enrollment import DeviceId, DeviceRegistry
from secretary_service.fixture_authority import FIXTURE_PUBLIC_KEY
from secretary_service.keys import DeterministicTestKeyProvider
from secretary_service.models import ActorId
from secretary_service.openclaw.contracts import (
    ALLOWED_CAPABILITIES,
    ALLOWED_ENDPOINTS,
    OPENCLAW_REVISION,
)
from secretary_service.voice.conversation import (
    RealtimeToolName,
    SpokenApprovalInterruption,
    VoiceConversationCoordinator,
    VoiceTurnInput,
)
from secretary_service.voice.e2e_models import (
    OpenClawVoiceFixture,
    RealtimeVoiceFixture,
    VoiceE2EError,
    VoiceE2EGolden,
    VoiceE2EReport,
)
from secretary_service.voice.e2e_support import (
    FixedClock,
    FixturePlanningBackend,
    FixtureProvider,
    require_gate,
)
from secretary_service.voice.journal import EncryptedTranscriptJournal
from secretary_service.voice.models import (
    AudioFrame,
    ProviderRetentionType,
    SessionStartRequest,
    VoiceSurface,
)
from secretary_service.voice.planning_bridge import (
    LifeEnginePlanningVoiceTools,
    VoicePlanningDependencies,
)
from secretary_service.voice.privacy import ConsentRegistry, RetentionVerification
from secretary_service.voice.session import VoiceServiceDependencies, VoiceSessionService

RESUME_CURSOR: Final = 4
EXPECTED_ARGUMENT_COUNT: Final = 5


def _validate_fabric(fabric: OpenClawVoiceFixture, audio: bytes) -> None:
    require_gate(condition=bool(audio) and len(audio) % 2 == 0, gate="PCM16 audio fixture")
    require_gate(condition=fabric.revision == OPENCLAW_REVISION, gate="OpenClaw revision")
    require_gate(
        condition=fabric.endpoints == tuple(item.value for item in ALLOWED_ENDPOINTS),
        gate="adapter endpoints",
    )
    require_gate(
        condition=fabric.capabilities == tuple(item.value for item in ALLOWED_CAPABILITIES),
        gate="adapter capabilities",
    )
    require_gate(
        condition=fabric.denied_realtime_endpoint not in {item.value for item in RealtimeToolName},
        gate="realtime tool allowlist",
    )


def _validate_report(report: VoiceE2EReport, golden: VoiceE2EGolden) -> None:
    require_gate(condition=report.event_ids == golden.event_ids, gate="continuous event IDs")
    require_gate(condition=report.tts == golden.tts, gate="golden TTS")
    require_gate(condition=report.transcripts == golden.transcripts, gate="continuous transcript")
    require_gate(
        condition=report.approved_replans == golden.approved_replans,
        gate="approved replan count",
    )
    require_gate(
        condition=report.execution_leases == golden.execution_leases,
        gate="execution lease count",
    )
    require_gate(
        condition=report.cancelled_approval_execution_leases
        == golden.cancelled_approval_execution_leases,
        gate="cancelled approval lease",
    )
    require_gate(
        condition=report.resume_event_ids == golden.resume_event_ids,
        gate="cross-device resume",
    )


def run_voice_e2e(
    audio_path: Path,
    relay_fixture_path: Path,
    openclaw_fixture_path: Path,
) -> VoiceE2EReport:
    """Drive the complete deterministic conversation through owned boundaries."""
    fixture = RealtimeVoiceFixture.model_validate_json(relay_fixture_path.read_text())
    fabric = OpenClawVoiceFixture.model_validate_json(openclaw_fixture_path.read_text())
    audio = audio_path.read_bytes()
    _validate_fabric(fabric, audio)
    clock = FixedClock(fixture.now)
    devices = DeviceRegistry(clock)
    device_id = DeviceId(str(fixture.devices[0].device_id))
    _ = devices.enroll(
        device_id, fixture.fingerprint, ActorId("user"), approval_public_key=FIXTURE_PUBLIC_KEY
    )
    consent = ConsentRegistry()
    consent.grant(VoiceSurface.PUSH_TO_TALK, fixture.now)
    provider = FixtureProvider()
    backend = FixturePlanningBackend(fixture)
    with tempfile.TemporaryDirectory(prefix="task-13-voice-") as directory:
        transcripts = EncryptedTranscriptJournal.open(
            Path(directory) / "voice.sqlite",
            DeterministicTestKeyProvider.from_seed(b"task-13-transcripts"),
            clock,
        )
        try:
            service = VoiceSessionService(
                VoiceServiceDependencies(
                    clock=clock,
                    devices=devices,
                    conversation_journal=ConversationJournal(
                        fixture.conversation,
                        list(fixture.devices),
                    ),
                    transcripts=transcripts,
                    consent=consent,
                    provider=provider,
                    endpoint=fixture.endpoint,
                    token_signing_key=b"task-13-session-token-key",
                    project_id=fixture.project_id,
                    retention=RetentionVerification(
                        project_id=fixture.project_id,
                        retention_type=ProviderRetentionType.ZERO_DATA_RETENTION,
                        store=False,
                        verified_at=fixture.now,
                        valid_until=fixture.now + timedelta(days=1),
                    ),
                )
            )
            tools = LifeEnginePlanningVoiceTools(
                VoicePlanningDependencies(
                    clock=clock,
                    matrix=default_approval_matrix(),
                    backend=backend,
                    signing_key=b"task-13-execution-lease-key",
                    devices=devices,
                )
            )
            coordinator = VoiceConversationCoordinator(service, tools)
            started = service.start(
                SessionStartRequest(
                    device_id=device_id,
                    public_key_fingerprint=fixture.fingerprint,
                    participant_id=fixture.devices[0].participant_id,
                    conversation_id=fixture.conversation.conversation_id,
                    surface=VoiceSurface.PUSH_TO_TALK,
                )
            )
            _ = service.append_audio(
                started.token.value,
                AudioFrame(event_id=1, turn_id=fixture.first_turn_id, pcm=audio),
            )
            first = coordinator.turn(
                started.token.value,
                VoiceTurnInput(
                    turn_id=fixture.first_turn_id,
                    partial_text="what's",
                    final_text="what's left",
                    tool=RealtimeToolName.REST_OF_DAY,
                ),
            )
            second = coordinator.turn(
                started.token.value,
                VoiceTurnInput(
                    turn_id=fixture.second_turn_id,
                    partial_text="move",
                    final_text="move workout",
                    tool=RealtimeToolName.REPLAN,
                ),
            )
            confirmation_id = second.confirmation_id
            if confirmation_id is None:
                gate = "replan confirmation"
                raise VoiceE2EError(gate)
            cancelled = coordinator.interrupt_spoken_approval(
                started.token.value,
                SpokenApprovalInterruption(
                    turn_id=fixture.second_turn_id,
                    confirmation_id=confirmation_id,
                ),
            )
            cancelled_leases = int(cancelled.execution_lease_id is not None)
            require_gate(condition=backend.applied == 0, gate="cancelled approval execution")
            approved = coordinator.approve_on_device(confirmation_id, fixture.approval)
            replayed = coordinator.approve_on_device(confirmation_id, fixture.approval)
            require_gate(condition=approved == replayed, gate="exactly-once replan")
            event_ids = (
                *(event.event_id for event in (*first.events, *second.events)),
                cancelled.cancellation_event.event_id,
            )
            spoken = (
                first.spoken_progress,
                first.spoken_reply,
                second.spoken_progress,
                second.spoken_reply,
                approved.spoken_text,
            )
            transcript_text = tuple(entry.text for entry in transcripts.entries())
            resumed = coordinator.resume_on_device(fixture.resume_device_id, RESUME_CURSOR)
            resume_ids = tuple(event.event_id for event in resumed.events)
            channel_sequences = tuple(
                event_id for event_id in event_ids if event_id > RESUME_CURSOR
            )
            require_gate(
                condition=service.snapshot(started.token.value).retained_audio_bytes == 0,
                gate="audio retention",
            )
            report = VoiceE2EReport(
                status="passed",
                event_ids=event_ids,
                tts=spoken,
                transcripts=transcript_text,
                approved_replans=coordinator.approved_replan_count,
                execution_leases=backend.applied,
                cancelled_approval_execution_leases=cancelled_leases,
                resume_event_ids=resume_ids,
                channel_bridge_sequences=channel_sequences,
                audio_sha256=hashlib.sha256(audio).hexdigest(),
                openclaw_revision=fabric.revision,
            )
            _validate_report(report, fixture.golden)
            return report
        finally:
            transcripts.close()


def main() -> int:
    """Run from the shell harness and write one exact JSON report."""
    if len(sys.argv) != EXPECTED_ARGUMENT_COUNT:
        gate = "arguments"
        raise VoiceE2EError(gate)
    report = run_voice_e2e(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
    _ = Path(sys.argv[4]).write_text(
        report.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
