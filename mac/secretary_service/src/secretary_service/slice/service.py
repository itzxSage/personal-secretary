"""Orchestration for the natural-language tomorrow planning vertical slice.

This module wires the Todo 2-7 surfaces together without reimplementing them:
Conversation API ordering, encrypted canonical capture, deterministic planner,
payload-bound proposal lifecycle, and the proposal-first Calendar adapter.
"""

import hmac
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Final, Protocol, final

if TYPE_CHECKING:
    from uuid import UUID

from secretary_service.authority import (
    Approval,
    ApprovalMatrix,
    ProposalLifecycle,
    ProposalRecord,
    ProposalState,
    Rollback,
    default_approval_matrix,
)
from secretary_service.conversation_api import (
    Conversation,
    ConversationAcceptance,
    ConversationDevice,
    ConversationEventKind,
    ConversationJournal,
    ConversationState,
    DeviceKind,
    TranscriptFinal,
)
from secretary_service.enrollment import DeviceRegistry
from secretary_service.google_calendar import GoogleCalendarAdapter
from secretary_service.google_calendar_contract import (
    CalendarEventDraft,
    CalendarPlan,
    calendar_payload,
)
from secretary_service.leases import LeaseIssuer, LeaseRequest, SigningKeyRing
from secretary_service.models import (
    ActorId,
    CorrelationId,
    NonEmpty,
    Proposal,
    RecordId,
    RecordKind,
    SourceItem,
    TransitionContext,
)
from secretary_service.planner import propose_day
from secretary_service.planner_models import DayPlanRequest
from secretary_service.planner_results import PlanBlockKind, ProposedDay
from secretary_service.slice.errors import (
    CaptureRejectedError,
    ProtectedConstraintViolationError,
    SliceProposalNotFoundError,
    UnsupportedCaptureError,
)
from secretary_service.slice.interpreter import InterpretationAdapter
from secretary_service.slice.models import (
    CaptureReceipt,
    ConversationCapture,
    InterpretationProposal,
    ReplanCommand,
    TomorrowApplyResult,
    TomorrowPreview,
    TomorrowReplanResult,
    TomorrowRollbackResult,
)
from secretary_service.storage import Clock, EncryptedStateStore

DEFAULT_APPROVAL_TTL: Final = timedelta(minutes=5)
DEFAULT_LEASE_TTL: Final = timedelta(minutes=10)
DEFAULT_ROLLBACK_TTL: Final = timedelta(minutes=5)
DEFAULT_ACTOR: Final = ActorId("lifeos")
DEFAULT_CORRELATION: Final = CorrelationId("tomorrow-slice")
DEFAULT_WORKER: Final = "lifeos-calendar-worker"
DEFAULT_CAPABILITY: Final = "calendar.apply"
DEFAULT_SIGNING_KEY: Final = b"task-8-tomorrow-slice-signing-key"
DEFAULT_PROVIDER_VERSION: Final = "lifeos.interpretation.v1"


class ProposalStore(Protocol):
    """Narrow persistence seam for in-flight slice proposals."""

    def save(
        self,
        proposal: ProposalRecord,
        plan_request: DayPlanRequest | None = None,
        provider_version: str | None = None,
    ) -> None:
        """Persist one proposal version with chained audit."""
        ...

    def load(self, proposal_id: RecordId) -> ProposalRecord | None:
        """Return the latest live proposal version."""
        ...

    def plan_request(self, proposal_id: RecordId) -> DayPlanRequest:
        """Return the canonical plan request bound to one proposal."""
        ...


@dataclass(frozen=True, slots=True)
class EncryptedProposalStore:
    """Audited proposal persistence over the encrypted state boundary."""

    store: EncryptedStateStore

    def save(
        self,
        proposal: ProposalRecord,
        plan_request: DayPlanRequest | None = None,
        provider_version: str | None = None,
    ) -> None:
        """Create or transition one proposal version with chained audit."""
        context = TransitionContext(
            actor=DEFAULT_ACTOR,
            correlation_id=DEFAULT_CORRELATION,
            occurred_at=proposal.created_at,
        )
        existing = self.store.read(RecordKind.PROPOSAL, proposal.proposal_id)
        if existing is None:
            if plan_request is None or provider_version is None:
                raise SliceProposalNotFoundError(proposal_id=proposal.proposal_id)
            self.store.create(
                Proposal(
                    record_id=proposal.proposal_id,
                    created_at=proposal.created_at,
                    state=proposal.state.value,
                    proposal_type=proposal.action_class,
                    payload=proposal.payload,
                    policy_decision=plan_request.model_dump_json(),
                    provider_version=provider_version,
                    reversible=True,
                    approval_state=proposal.state.value,
                ),
                context,
            )
            return
        self.store.transition(
            RecordKind.PROPOSAL,
            proposal.proposal_id,
            proposal.state.value,
            context,
        )

    def load(self, proposal_id: RecordId) -> ProposalRecord | None:
        """Return the latest live proposal version."""
        record = self.store.read(RecordKind.PROPOSAL, proposal_id)
        if record is None:
            return None
        assert isinstance(record, Proposal)  # noqa: S101 - store.read narrows to the requested kind
        return ProposalRecord(
            proposal_id=proposal_id,
            action_class=record.proposal_type,
            payload=record.payload,
            state=ProposalState(record.state),
            created_at=record.created_at,
        )

    def plan_request(self, proposal_id: RecordId) -> DayPlanRequest:
        """Return the canonical plan request bound to one proposal."""
        record = self.store.read(RecordKind.PROPOSAL, proposal_id)
        if record is None or not isinstance(record, Proposal):
            raise SliceProposalNotFoundError(proposal_id=proposal_id)
        return DayPlanRequest.model_validate_json(record.policy_decision)


@final
class TomorrowPlanningSlice:
    """Deterministic capture-to-preview/apply/replan orchestration."""

    def __init__(  # noqa: PLR0913 - orchestration binds six immutable dependencies
        self,
        *,
        clock: Clock,
        store: EncryptedStateStore,
        interpreter: InterpretationAdapter,
        calendar: GoogleCalendarAdapter,
        devices: DeviceRegistry,
        matrix: ApprovalMatrix | None = None,
        proposal_store: ProposalStore | None = None,
    ) -> None:
        """Bind immutable orchestration dependencies."""
        self._clock = clock
        self._store = store
        self._interpreter = interpreter
        self._calendar = calendar
        self._matrix = matrix or default_approval_matrix()
        self._lifecycle = ProposalLifecycle(clock, devices, store.authority_consumption)
        self._keys = SigningKeyRing(DEFAULT_SIGNING_KEY)
        self._leases = LeaseIssuer(clock, self._keys, DEFAULT_LEASE_TTL)
        self._proposals = proposal_store or EncryptedProposalStore(store)

    def preview(self, capture: ConversationCapture) -> TomorrowPreview:
        """Interpret one capture and produce an audited, non-mutating preview."""
        receipt = self._accept_capture(capture)
        interpretation = self._interpreter.interpret(receipt.text, receipt.accepted_event_ids[-1])
        plan = propose_day(interpretation.plan_request)
        proposal = self._propose(interpretation, plan)
        calendar_plan = self._calendar_plan(proposal, plan)
        calendar = self._calendar.dry_run(calendar_plan)
        self._proposals.save(
            proposal,
            interpretation.plan_request,
            interpretation.provider_version,
        )
        return TomorrowPreview(
            capture=receipt,
            interpretation=interpretation,
            authorization=proposal,
            plan=plan,
            calendar=calendar,
        )

    def approve(self, proposal_id: RecordId, approval: Approval) -> TomorrowApplyResult:
        """Approve and apply one payload-bound calendar proposal."""
        proposal = self._require(proposal_id)
        state = self._lifecycle.approve(proposal, approval, self._matrix)
        approved = proposal.model_copy(update={"state": state})
        self._proposals.save(approved)
        lease = self._leases.issue(
            approved,
            LeaseRequest(
                capability=DEFAULT_CAPABILITY,
                worker_id=DEFAULT_WORKER,
                actor=DEFAULT_ACTOR,
                correlation_id=DEFAULT_CORRELATION,
                idempotency_key=str(approval.fact_id),
            ),
        )
        _ = self._leases.verify(lease, approved)
        plan = self._calendar_plan(approved, self._plan_for(proposal_id))
        calendar = self._calendar.apply(plan)
        applied_state = self._lifecycle.apply(approved, lease)
        applied = approved.model_copy(update={"state": applied_state})
        self._proposals.save(applied)
        return TomorrowApplyResult(authorization=applied, calendar=calendar)

    def replan(self, command: ReplanCommand) -> TomorrowReplanResult:
        """Replan after a changed schedule while preserving protected intervals."""
        previous = self._require(command.previous_proposal_id)
        if previous.state != ProposalState.APPLIED:
            raise SliceProposalNotFoundError(proposal_id=command.previous_proposal_id)
        receipt = self._accept_capture(command.capture)
        interpretation = self._interpreter.interpret(receipt.text, receipt.accepted_event_ids[-1])
        plan = propose_day(interpretation.plan_request)
        self._assert_protected_preserved(previous, plan)
        proposal = self._propose(interpretation, plan)
        calendar_plan = self._calendar_plan(proposal, plan)
        calendar = self._calendar.dry_run(calendar_plan)
        self._proposals.save(
            proposal,
            interpretation.plan_request,
            interpretation.provider_version,
        )
        return TomorrowReplanResult(
            previous_proposal_id=command.previous_proposal_id,
            preview=TomorrowPreview(
                capture=receipt,
                interpretation=interpretation,
                authorization=proposal,
                plan=plan,
                calendar=calendar,
            ),
            preserved_protected_activity_ids=self._protected_ids(plan),
        )

    def rollback(self, proposal_id: RecordId, rollback: Rollback) -> TomorrowRollbackResult:
        """Revert one applied proposal and delete only its owned events."""
        proposal = self._require(proposal_id)
        if proposal.state != ProposalState.APPLIED:
            raise SliceProposalNotFoundError(proposal_id=proposal_id)
        plan = self._calendar_plan(proposal, self._plan_for(proposal_id))
        calendar = self._calendar.rollback(plan, rollback)
        state = self._lifecycle.revert(proposal, rollback)
        reverted = proposal.model_copy(update={"state": state})
        self._proposals.save(reverted)
        return TomorrowRollbackResult(authorization=reverted, calendar=calendar)

    def cleanup(self, proposal_id: RecordId, rollback: Rollback) -> TomorrowRollbackResult:
        """Idempotent E2E cleanup that removes every owned sandbox event."""
        return self.rollback(proposal_id, rollback)

    def _accept_capture(self, capture: ConversationCapture) -> CaptureReceipt:
        first = capture.events[0]
        journal = ConversationJournal(
            Conversation(
                conversation_id=first.conversation_id,
                title="Tomorrow planning",
                participant_ids=[first.participant_id],
                device_ids=[first.device_id],
                created_at=first.occurred_at,
                state=ConversationState.ACTIVE,
            ),
            [
                ConversationDevice(
                    device_id=first.device_id,
                    participant_id=first.participant_id,
                    kind=DeviceKind.IOS,
                    name="iPhone",
                    created_at=first.occurred_at,
                )
            ],
        )
        accepted: list[UUID] = []
        for event in capture.events:
            result: ConversationAcceptance = journal.accept(event)
            if not result.accepted:
                assert result.rejection is not None  # noqa: S101 - rejection is present when not accepted
                raise CaptureRejectedError(rejection=result.rejection)
            accepted.append(event.event_id)
        final_event = next(
            (
                event
                for event in capture.events
                if event.kind is ConversationEventKind.TRANSCRIPT_FINAL
            ),
            None,
        )
        if final_event is None:
            raise UnsupportedCaptureError(event_kind="no-final-transcript")
        payload = final_event.payload
        assert isinstance(payload, TranscriptFinal)  # noqa: S101 - kind check guarantees the payload type
        text = payload.text
        source_id = RecordId(final_event.event_id)
        fingerprint = hmac.digest(b"task-8-tomorrow-slice", text.encode(), "sha256").hex()
        self._store.create(
            SourceItem(
                record_id=source_id,
                created_at=final_event.occurred_at,
                source_type="communication",
                external_id=str(final_event.event_id),
                raw_content=text,
            ),
            TransitionContext(
                actor=DEFAULT_ACTOR,
                correlation_id=DEFAULT_CORRELATION,
                occurred_at=final_event.occurred_at,
            ),
        )
        return CaptureReceipt(
            accepted_event_ids=tuple(accepted),
            next_sequence=len(capture.events) + 1,
            source_record_id=source_id,
            text=text,
            source_fingerprint=fingerprint,
        )

    def _propose(self, interpretation: InterpretationProposal, plan: ProposedDay) -> ProposalRecord:
        events = self._calendar_events(plan)
        return ProposalRecord(
            proposal_id=interpretation.proposal_id,
            action_class="calendar.apply",
            payload=calendar_payload(events),
            state=ProposalState.PROPOSED,
            created_at=self._clock.now(),
        )

    def _calendar_events(self, plan: ProposedDay) -> tuple[CalendarEventDraft, ...]:
        return tuple(
            CalendarEventDraft(
                event_key=block.block_id.replace(":", "-"),
                summary=block.title,
                starts_at=block.starts_at,
                ends_at=block.ends_at,
            )
            for block in plan.calendar_projection
        )

    def _calendar_plan(self, proposal: ProposalRecord, plan: ProposedDay) -> CalendarPlan:
        return CalendarPlan(
            authorization=proposal,
            events=self._calendar_events(plan),
        )

    def _plan_for(self, proposal_id: RecordId) -> ProposedDay:
        request = self._proposals.plan_request(proposal_id)
        return propose_day(request)

    def _require(self, proposal_id: RecordId) -> ProposalRecord:
        proposal = self._proposals.load(proposal_id)
        if proposal is None:
            raise SliceProposalNotFoundError(proposal_id=proposal_id)
        return proposal

    def _assert_protected_preserved(self, previous: ProposalRecord, plan: ProposedDay) -> None:
        previous_plan = self._plan_for(previous.proposal_id)
        previous_protected = {
            block.activity_id
            for block in previous_plan.internal_plan
            if block.kind is PlanBlockKind.PROTECTED and block.activity_id is not None
        }
        current_protected = {
            block.activity_id
            for block in plan.internal_plan
            if block.kind is PlanBlockKind.PROTECTED and block.activity_id is not None
        }
        missing = previous_protected - current_protected
        if missing:
            raise ProtectedConstraintViolationError(activity_id=min(missing))

    def _protected_ids(self, plan: ProposedDay) -> tuple[NonEmpty, ...]:
        return tuple(
            block.activity_id
            for block in plan.internal_plan
            if block.kind is PlanBlockKind.PROTECTED and block.activity_id is not None
        )
