"""Task 13 integration tests for continuous owned voice conversations."""

from dataclasses import dataclass
from pathlib import Path
from typing import final
from uuid import UUID

import pytest

from secretary_service.authority import (
    Approval,
    ApprovalProof,
    ProposalRecord,
    ProposalState,
    default_approval_matrix,
)
from secretary_service.channels.webchat import WebChatAdapter, WebChatMessage
from secretary_service.conversation_api import ChannelKind
from secretary_service.enrollment import DeviceId, DeviceRegistry
from secretary_service.fixture_authority import FIXTURE_PUBLIC_KEY, sign_fixture_approval
from secretary_service.models import ActorId, CorrelationId, RecordId
from secretary_service.voice.channel_bridge import OpenClawChannelBridge
from secretary_service.voice.conversation import (
    ApprovedReplan,
    LifeEngineToolReply,
    RealtimeToolAccessError,
    RealtimeToolName,
    RealtimeToolRequest,
    SpokenApprovalInterruption,
    VoiceConversationCoordinator,
    VoiceTurnInput,
)
from secretary_service.voice.planning_bridge import (
    LifeEnginePlanningVoiceTools,
    VoicePlanningDependencies,
)
from tests.helpers import FakeClock
from tests.test_channel_continuity import START, make_channel_fixture
from tests.test_voice_session import FINGERPRINT, NOW, TURN_ID, build_harness
from tests.voice_test_support import voice_start_request

SECOND_TURN_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
RESUME_DEVICE_ID = UUID("77777777-7777-4777-8777-777777777777")
PROPOSAL_ID = RecordId(UUID("88888888-8888-4888-8888-888888888888"))
CONFIRMATION_ID = UUID("99999999-9999-4999-8999-999999999999")
LEASE_ID = RecordId(UUID("aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"))


@final
class RecordingTools:
    def __init__(self) -> None:
        self.requests: list[RealtimeToolRequest] = []
        self.approvals = 0

    def invoke(self, request: RealtimeToolRequest) -> LifeEngineToolReply:
        self.requests.append(request)
        replies = {
            RealtimeToolName.REST_OF_DAY: LifeEngineToolReply(
                spoken_text="Workout is the only flexible item left today.",
            ),
            RealtimeToolName.REPLAN: LifeEngineToolReply(
                spoken_text="I can move the workout to 6 PM. Confirm on your iPhone.",
                proposal_id=PROPOSAL_ID,
                confirmation_id=CONFIRMATION_ID,
            ),
        }
        return replies[request.tool]

    def approve(self, proposal_id: RecordId, approval: Approval) -> ApprovedReplan:
        assert proposal_id == PROPOSAL_ID
        assert approval.proof is ApprovalProof.DEVICE_SIGNED
        self.approvals += 1
        return ApprovedReplan(
            proposal_id=proposal_id,
            spoken_text="Workout moved to 6 PM.",
            execution_lease_id=LEASE_ID,
        )


@dataclass(frozen=True, slots=True)
class RecordingChannelBridge:
    sequences: tuple[int, ...]

    def synchronize(self, after_sequence: int) -> tuple[int, ...]:
        return tuple(sequence for sequence in self.sequences if sequence > after_sequence)


@final
class RecordingPlanningBackend:
    def __init__(self, proposal: ProposalRecord) -> None:
        self.proposal = proposal
        self.applied = 0

    def rest_of_day(self) -> str:
        return "Workout is the only flexible item left today."

    def propose_replan(self, text: str) -> ProposalRecord:
        assert text == "move workout"
        return self.proposal

    def apply_replan(self, proposal: ProposalRecord) -> str:
        assert proposal.state is ProposalState.APPROVED
        self.applied += 1
        return "Workout moved to 6 PM."


def device_approval() -> Approval:
    return Approval(
        fact_id=RecordId(UUID("bbbbbbbb-1111-4111-8111-bbbbbbbbbbbb")),
        proposal_id=PROPOSAL_ID,
        payload_hash="fixture-payload-hash",
        issued_at=NOW,
        expires_at=NOW.replace(minute=5),
        idempotency_key="task-13-device-approval",
        actor=ActorId("user"),
        correlation_id=CorrelationId("task-13"),
        proof=ApprovalProof.DEVICE_SIGNED,
        device_id=DeviceId("iphone-owner"),
    )


def test_continuous_turns_speak_progress_and_reply_with_contiguous_ids(tmp_path: Path) -> None:
    # Given: one owned relay session and Life Engine's closed realtime tool router.
    harness = build_harness(tmp_path)
    tools = RecordingTools()
    coordinator = VoiceConversationCoordinator(harness.service, tools)
    try:
        started = harness.service.start(voice_start_request(FINGERPRINT))

        # When: the user asks what remains, then requests a workout move.
        first = coordinator.turn(
            started.token.value,
            VoiceTurnInput(
                turn_id=TURN_ID,
                partial_text="what's",
                final_text="what's left",
                tool=RealtimeToolName.REST_OF_DAY,
            ),
        )
        second = coordinator.turn(
            started.token.value,
            VoiceTurnInput(
                turn_id=SECOND_TURN_ID,
                partial_text="move",
                final_text="move workout",
                tool=RealtimeToolName.REPLAN,
            ),
        )

        # Then: both turns are one continuous stream with deterministic spoken output.
        assert [event.event_id for event in (*first.events, *second.events)] == list(range(1, 7))
        assert first.spoken_progress == "Checking your Life Plan."
        assert first.spoken_reply == "Workout is the only flexible item left today."
        assert second.confirmation_id == CONFIRMATION_ID
        assert [request.tool for request in tools.requests] == [
            RealtimeToolName.REST_OF_DAY,
            RealtimeToolName.REPLAN,
        ]
    finally:
        harness.transcripts.close()


def test_interrupted_spoken_approval_creates_no_lease_and_device_approval_is_once(
    tmp_path: Path,
) -> None:
    # Given: a pending replan confirmation produced by the owned Life Engine.
    harness = build_harness(tmp_path)
    tools = RecordingTools()
    coordinator = VoiceConversationCoordinator(harness.service, tools)
    try:
        started = harness.service.start(voice_start_request(FINGERPRINT))
        _ = coordinator.turn(
            started.token.value,
            VoiceTurnInput(
                turn_id=SECOND_TURN_ID,
                partial_text="move",
                final_text="move workout",
                tool=RealtimeToolName.REPLAN,
            ),
        )

        # When: spoken approval is interrupted before a device-signed confirmation arrives.
        cancelled = coordinator.interrupt_spoken_approval(
            started.token.value,
            SpokenApprovalInterruption(
                turn_id=SECOND_TURN_ID,
                confirmation_id=CONFIRMATION_ID,
            ),
        )

        # Then: speech proves no identity and creates no execution lease.
        assert cancelled.execution_lease_id is None
        assert cancelled.spoken_approval_valid is False
        assert tools.approvals == 0

        # When: the enrolled iPhone later provides its device-signed approval twice.
        first = coordinator.approve_on_device(CONFIRMATION_ID, device_approval())
        duplicate = coordinator.approve_on_device(CONFIRMATION_ID, device_approval())

        # Then: exactly one replan and one lease exist; replay returns the same result.
        assert first == duplicate
        assert first.execution_lease_id == LEASE_ID
        assert coordinator.approved_replan_count == 1
        assert tools.approvals == 1
    finally:
        harness.transcripts.close()


def test_non_life_engine_tool_is_rejected_before_dispatch(tmp_path: Path) -> None:
    # Given: an active coordinator with no tool calls yet.
    harness = build_harness(tmp_path)
    tools = RecordingTools()
    coordinator = VoiceConversationCoordinator(harness.service, tools)
    try:
        # When: realtime asks for an endpoint outside Life Engine's allowlist.
        with pytest.raises(RealtimeToolAccessError):
            _ = coordinator.endpoint_access("openclaw.worker.execute")

        # Then: the request never reaches an adapter or tool implementation.
        assert tools.requests == []
    finally:
        harness.transcripts.close()


def test_cross_device_resume_and_optional_channel_bridge_replay_only_missed_events(
    tmp_path: Path,
) -> None:
    # Given: a completed first voice turn and optional OpenClaw canonical sync bridge.
    harness = build_harness(tmp_path)
    coordinator = VoiceConversationCoordinator(harness.service, RecordingTools())
    try:
        started = harness.service.start(voice_start_request(FINGERPRINT))
        _ = coordinator.turn(
            started.token.value,
            VoiceTurnInput(
                turn_id=TURN_ID,
                partial_text="what's",
                final_text="what's left",
                tool=RealtimeToolName.REST_OF_DAY,
            ),
        )

        # When: another owned device resumes after event one and a channel syncs after four.
        resumed = coordinator.resume_on_device(RESUME_DEVICE_ID, after_event_id=1)
        channel = coordinator.bridge_channel(RecordingChannelBridge((1, 2, 3, 4, 5, 6)), 4)

        # Then: both adapters replay from Life Engine cursors without owning canonical state.
        assert resumed.device_id == RESUME_DEVICE_ID
        assert [event.event_id for event in resumed.events] == [2, 3]
        assert channel == (5, 6)
    finally:
        harness.transcripts.close()


def test_planning_bridge_issues_real_lease_only_after_device_signed_approval() -> None:
    # Given: a deterministic planner proposal behind Life Engine's policy boundary.
    proposal = ProposalRecord(
        proposal_id=PROPOSAL_ID,
        action_class="calendar.apply",
        payload='{"activity":"workout","start":"18:00"}',
        state=ProposalState.PROPOSED,
        created_at=NOW,
    )
    clock = FakeClock(NOW)
    devices = DeviceRegistry(clock)
    _ = devices.enroll(
        DeviceId("iphone-owner"),
        "voice-test-iphone-1",
        ActorId("user"),
        approval_public_key=FIXTURE_PUBLIC_KEY,
    )
    backend = RecordingPlanningBackend(proposal)
    tools = LifeEnginePlanningVoiceTools(
        VoicePlanningDependencies(
            clock=clock,
            matrix=default_approval_matrix(),
            backend=backend,
            signing_key=b"task-13-life-engine-lease-key",
            devices=devices,
        )
    )
    reply = tools.invoke(
        RealtimeToolRequest(
            tool=RealtimeToolName.REPLAN,
            turn_id=SECOND_TURN_ID,
            text="move workout",
        )
    )
    approval = device_approval().model_copy(update={"payload_hash": proposal.payload_hash()})

    # When: the enrolled device signs the exact proposed payload.
    applied = tools.approve(PROPOSAL_ID, sign_fixture_approval(approval))

    # Then: the backend applies once under a concrete one-shot execution lease.
    assert reply.proposal_id == PROPOSAL_ID
    assert applied.execution_lease_id is not None
    assert backend.applied == 1


def test_openclaw_channel_bridge_reads_only_canonical_life_engine_events() -> None:
    # Given: an owner-bound optional WebChat adapter at the typed OpenClaw boundary.
    fixture = make_channel_fixture(ChannelKind.OPENCLAW_WEBCHAT)
    adapter = WebChatAdapter(fixture.dependencies)
    binding = fixture.identity.binding
    _ = adapter.receive(
        WebChatMessage(
            message_id="task-13-channel-turn",
            conversation_id=binding.external_channel_id,
            user_id=binding.external_user_id,
            sent_at=START,
            text="what's left",
        )
    )

    # When: Task 13 resumes the channel after its durable cursor.
    sequences = OpenClawChannelBridge(adapter).synchronize(after_sequence=0)

    # Then: the bridge projects canonical sequences and grants no realtime tool endpoint.
    assert sequences == (1, 2)
