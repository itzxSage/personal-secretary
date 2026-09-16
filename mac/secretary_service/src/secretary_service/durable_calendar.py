"""Approval-to-outbox transaction for the cloud calendar migration.

No provider calls or public route are enabled. Account-backed enrollment and
worker revalidation are prerequisites before this service can execute live work.
"""

import fcntl
import os
from collections.abc import Generator
from contextlib import contextmanager
from uuid import uuid4

from pydantic import JsonValue, TypeAdapter

from secretary_service.authority import (
    Approval,
    PolicyViolationError,
    ProposalLifecycle,
    ProposalRecord,
    ProposalState,
    ResetRequest,
    default_approval_matrix,
)
from secretary_service.enrollment import DeviceRegistry
from secretary_service.google_calendar import GoogleCalendarAdapter
from secretary_service.google_calendar_contract import (
    CalendarEventDraft,
    CalendarOperation,
    CalendarPlan,
)
from secretary_service.models import (
    ActorId,
    CorrelationId,
    Execution,
    FrozenModel,
    NonEmpty,
    Proposal,
    RecordId,
    RecordKind,
    TransitionContext,
)
from secretary_service.postgres_outbox import PendingCalendarOperation
from secretary_service.postgres_store import PostgresStateStore
from secretary_service.storage import Clock, EncryptedStateStore


def approve_and_enqueue_calendar(
    store: PostgresStateStore, approval: Approval, clock: Clock
) -> PendingCalendarOperation:
    """Verify the current stored payload and signed proof before one atomic commit."""
    with store.execution_transaction() as unit:
        current = unit.records.read(RecordKind.PROPOSAL, approval.proposal_id)
        if not isinstance(current, Proposal) or current.proposal_type != "calendar.apply":
            message = "stored calendar proposal required"
            raise PolicyViolationError(message)
        proposal = ProposalRecord(
            proposal_id=current.record_id,
            action_class="calendar.apply",
            payload=current.payload,
            state=ProposalState(current.state),
            created_at=current.created_at,
        )
        lifecycle = ProposalLifecycle(clock, DeviceRegistry(clock, unit.devices), unit.consumption)
        _ = lifecycle.approve(proposal, approval, default_approval_matrix())
        context = TransitionContext(
            actor=approval.actor, correlation_id=approval.correlation_id, occurred_at=clock.now()
        )
        unit.records.transition(RecordKind.PROPOSAL, current.record_id, "approved", context)
        job = PendingCalendarOperation(
            operation_id=approval.fact_id,
            proposal_id=proposal.proposal_id,
            payload=proposal.payload,
            approval=approval,
            authorized_until=approval.expires_at,
        )
        unit.outbox.enqueue(job, context)
        return job


def claim_verified_calendar(
    store: PostgresStateStore, clock: Clock
) -> PendingCalendarOperation | None:
    """Recheck current proposal/device state before returning a claimed job.

    This is a persistence handoff, not a provider executor or worker capability
    lease. Live execution remains disabled until the execution boundary is wired.
    """
    with store.execution_transaction() as unit:
        context = TransitionContext(
            actor=ActorId("calendar-dispatch"),
            correlation_id=CorrelationId("calendar-claim"),
            occurred_at=clock.now(),
        )
        while (job := unit.outbox.claim(context)) is not None:
            if job.claim_token is None:
                raise AssertionError
            stored = unit.records.read(RecordKind.PROPOSAL, job.proposal_id)
            valid = (
                isinstance(stored, Proposal)
                and stored.proposal_type == "calendar.apply"
                and stored.state == "approved"
                and stored.payload == job.payload
            )
            if valid:
                proposal = ProposalRecord(
                    proposal_id=job.proposal_id,
                    action_class="calendar.apply",
                    payload=job.payload,
                    state=ProposalState.APPROVED,
                    created_at=job.approval.issued_at,
                )
                try:
                    ProposalLifecycle(clock, DeviceRegistry(clock, unit.devices)).validate_approval(
                        proposal, job.approval, default_approval_matrix()
                    )
                except PolicyViolationError:
                    valid = False
            if valid:
                return job
            unit.outbox.cancel_claim(job.operation_id, job.claim_token, context)
        return None


class CalendarRecoveryOutcome(FrozenModel):
    """Result of interrupted-apply recovery after provider reconciliation."""

    proposal_id: RecordId
    state: ProposalState
    outcome: NonEmpty
    succeeded_events: tuple[str, ...]
    missing_events: tuple[str, ...]


@contextmanager
def calendar_execution_lock(store: EncryptedStateStore) -> Generator[None]:
    """Serialize provider execution and reconciliation across relay processes.

    The OS releases this lock on process death. Database authorization commits
    happen before provider calls, so a crash preserves consumed authority.
    """
    descriptor = os.open(str(store.path) + ".calendar.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def recover_interrupted_apply(
    store: EncryptedStateStore,
    proposal_id: RecordId,
    adapter: GoogleCalendarAdapter,
    reset_request: ResetRequest,
    clock: Clock,
) -> CalendarRecoveryOutcome:
    """Reconcile under the execution lock before an authorized zero-effect reset."""
    with calendar_execution_lock(store):
        return reconcile_interrupted_apply(store, proposal_id, adapter, reset_request, clock)


def reconcile_interrupted_apply(
    store: EncryptedStateStore,
    proposal_id: RecordId,
    adapter: GoogleCalendarAdapter,
    reset_request: ResetRequest,
    clock: Clock,
) -> CalendarRecoveryOutcome:
    """Reconcile while the caller holds the external execution lock."""
    with store.domain_transaction() as records:
        current = records.read(RecordKind.PROPOSAL, proposal_id)
        if not isinstance(current, Proposal) or current.proposal_type != "calendar.apply":
            message = "stored calendar proposal required"
            raise PolicyViolationError(message)
        if current.state != "approved":
            message = f"cannot recover proposal in state {current.state}"
            raise PolicyViolationError(message)
        proposal = ProposalRecord(
            proposal_id=current.record_id,
            action_class="calendar.apply",
            payload=current.payload,
            state=ProposalState.APPROVED,
            created_at=current.created_at,
        )
        lifecycle = ProposalLifecycle(
            clock, DeviceRegistry(clock, store.devices), store.authority_consumption
        )
        lifecycle.validate_approval(proposal, reset_request, default_approval_matrix())
        try:
            raw_events = TypeAdapter(list[JsonValue]).validate_json(current.payload)
        except ValueError as error:
            message = "stored calendar proposal payload must be an event list"
            raise PolicyViolationError(message) from error
        events = tuple(CalendarEventDraft.model_validate(event) for event in raw_events)
        plan = CalendarPlan(authorization=proposal, events=events)
        reconciliation = adapter.reconcile(plan, None)
        succeeded = tuple(
            sorted(
                diff.event_id
                for diff in reconciliation.operations
                if diff.operation == CalendarOperation.NOOP
            )
        )
        missing = tuple(
            sorted(
                diff.event_id
                for diff in reconciliation.operations
                if diff.operation == CalendarOperation.MISSING
            )
        )
        # Existing but changed/conflicting events are uncertain, never success or zero effects.
        conflict = any(
            diff.operation not in (CalendarOperation.NOOP, CalendarOperation.MISSING)
            for diff in reconciliation.operations
        )
        expected = {diff.event_id for diff in reconciliation.operations}
        conflict = conflict or any(
            event.ownership is not None
            and event.ownership.proposal_id == str(proposal_id)
            and event.event_id not in expected
            for event in reconciliation.sync_state.events
        )
        state = ProposalState.APPROVED
        outcome = "conflict" if conflict else "partial"
        if not conflict and not missing:
            state, outcome = ProposalState.APPLIED, "applied"
        elif not conflict and not succeeded:
            state = lifecycle.reset(
                proposal, reset_request, default_approval_matrix(), zero_effects_verified=True
            )
            outcome = "reset"
        result = CalendarRecoveryOutcome(
            proposal_id=proposal_id,
            state=state,
            outcome=outcome,
            succeeded_events=succeeded,
            missing_events=missing,
        )
        context = TransitionContext(
            actor=reset_request.actor,
            correlation_id=reset_request.correlation_id,
            occurred_at=clock.now(),
        )
        records.create(
            Execution(
                record_id=RecordId(uuid4()),
                created_at=clock.now(),
                state="recorded",
                proposal_id=proposal_id,
                outcome=result.model_dump_json(),
            ),
            context,
        )
        if state != ProposalState.APPROVED:
            records.transition(RecordKind.PROPOSAL, proposal_id, state.value, context)
        return result
