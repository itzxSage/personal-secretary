"""Approval-to-outbox transaction for the cloud calendar migration.

No provider calls or public route are enabled. Account-backed enrollment and
worker revalidation are prerequisites before this service can execute live work.
"""

from secretary_service.authority import (
    Approval,
    PolicyViolationError,
    ProposalLifecycle,
    ProposalRecord,
    ProposalState,
    default_approval_matrix,
)
from secretary_service.enrollment import DeviceRegistry
from secretary_service.models import ActorId, CorrelationId, Proposal, RecordKind, TransitionContext
from secretary_service.postgres_outbox import PendingCalendarOperation
from secretary_service.postgres_store import PostgresStateStore
from secretary_service.storage import Clock


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
