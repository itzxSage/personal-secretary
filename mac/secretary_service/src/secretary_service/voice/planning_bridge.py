"""Authority-preserving bridge from realtime turns to Life Engine planning."""

from dataclasses import dataclass
from datetime import timedelta
from typing import Final, Protocol, final
from uuid import UUID

from secretary_service.authority import (
    Approval,
    ApprovalMatrix,
    ProposalLifecycle,
    ProposalRecord,
    ProposalState,
)
from secretary_service.enrollment import DeviceRegistry
from secretary_service.leases import LeaseIssuer, LeaseRequest, SigningKeyRing
from secretary_service.models import ActorId, CorrelationId, RecordId
from secretary_service.storage import Clock
from secretary_service.voice.conversation import (
    ApprovedReplan,
    LifeEngineToolReply,
    RealtimeToolName,
    RealtimeToolRequest,
)

VOICE_REPLAN_CAPABILITY: Final = "calendar.apply"
VOICE_REPLAN_WORKER: Final = "lifeos-calendar-worker"
VOICE_REPLAN_ACTOR: Final = ActorId("lifeos")
VOICE_REPLAN_CORRELATION: Final = CorrelationId("voice-conversation")
DEFAULT_VOICE_LEASE_TTL: Final = timedelta(minutes=10)


class VoicePlanningBackend(Protocol):
    """Planner/slice operations that remain owned by the Life Engine."""

    def rest_of_day(self) -> str:
        """Return a spoken summary of canonical plan state."""
        ...

    def propose_replan(self, text: str) -> ProposalRecord:
        """Return a non-mutating proposal produced by the deterministic planner."""
        ...

    def apply_replan(self, proposal: ProposalRecord) -> str:
        """Apply one lease-authorized proposal and return its spoken result."""
        ...


@dataclass(frozen=True, slots=True)
class VoicePlanningDependencies:
    """Policy and planner boundaries for realtime planning requests."""

    clock: Clock
    matrix: ApprovalMatrix
    backend: VoicePlanningBackend
    signing_key: bytes
    devices: DeviceRegistry


@final
class LifeEnginePlanningVoiceTools:
    """Issue planner actions only through approval and a one-shot lease."""

    def __init__(self, dependencies: VoicePlanningDependencies) -> None:
        """Bind planning, policy, clock, and signing-key boundaries."""
        self._dependencies = dependencies
        self._lifecycle = ProposalLifecycle(dependencies.clock, dependencies.devices)
        self._leases = LeaseIssuer(
            dependencies.clock,
            SigningKeyRing(dependencies.signing_key),
            lease_ttl=DEFAULT_VOICE_LEASE_TTL,
        )
        self._proposals: dict[RecordId, ProposalRecord] = {}

    def invoke(self, request: RealtimeToolRequest) -> LifeEngineToolReply:
        """Read plan state or create a proposal without granting authority."""
        match request.tool:  # noqa: MATCH_OK - basedpyright proves enum exhaustiveness.
            case RealtimeToolName.REST_OF_DAY:
                return LifeEngineToolReply(
                    spoken_text=self._dependencies.backend.rest_of_day(),
                )
            case RealtimeToolName.REPLAN:
                proposal = self._dependencies.backend.propose_replan(request.text)
                self._proposals[proposal.proposal_id] = proposal
                return LifeEngineToolReply(
                    spoken_text="I have a replan ready. Confirm on your iPhone.",
                    proposal_id=proposal.proposal_id,
                    confirmation_id=UUID(str(proposal.proposal_id)),
                )

    def approve(self, proposal_id: RecordId, approval: Approval) -> ApprovedReplan:
        """Apply a proposal only after device proof and lease verification."""
        proposal = self._proposals[proposal_id]
        state = self._lifecycle.approve(proposal, approval, self._dependencies.matrix)
        approved = proposal.model_copy(update={"state": state})
        lease = self._leases.issue(
            approved,
            LeaseRequest(
                capability=VOICE_REPLAN_CAPABILITY,
                worker_id=VOICE_REPLAN_WORKER,
                actor=VOICE_REPLAN_ACTOR,
                correlation_id=VOICE_REPLAN_CORRELATION,
                idempotency_key=approval.idempotency_key,
            ),
        )
        _ = self._leases.verify(lease, approved)
        spoken_text = self._dependencies.backend.apply_replan(approved)
        applied_state = self._lifecycle.apply(approved, lease)
        assert applied_state is ProposalState.APPLIED  # noqa: S101 - lifecycle contract
        return ApprovedReplan(
            proposal_id=proposal_id,
            spoken_text=spoken_text,
            execution_lease_id=lease.fact_id,
        )
