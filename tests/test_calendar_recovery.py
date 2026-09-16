"""Calendar recovery with reconciliation-before-reset tests."""

import base64
import hashlib
from datetime import timedelta
from uuid import uuid4

import pytest

from secretary_service.authority import (
    PolicyViolationError,
    ProposalLifecycle,
    ProposalRecord,
    ProposalState,
    ResetRequest,
    default_approval_matrix,
)
from secretary_service.durable_calendar import recover_interrupted_apply
from secretary_service.enrollment import DeviceRegistry
from secretary_service.fixture_authority import FIXTURE_PRIVATE_KEY, FIXTURE_PUBLIC_KEY
from secretary_service.google_calendar import (
    CalendarEventDraft,
    CalendarOperation,
    CalendarPlan,
    calendar_payload,
)
from secretary_service.models import Proposal, RecordId, RecordKind, TransitionContext
from secretary_service.storage import EncryptedStateStore
from tests.helpers import FakeClock
from tests.test_authority import (
    ACTOR,
    CORRELATION,
    DEFAULT_DEVICE_ID,
    make_approval,
    make_lease,
)
from tests.test_google_calendar import make_adapter


def make_reset(clock: FakeClock, proposal: ProposalRecord, *, idempotency_key: str) -> ResetRequest:
    proof = ResetRequest.model_validate(
        make_approval(clock, proposal, idempotency_key=idempotency_key).model_dump()
    )
    return proof.model_copy(
        update={"signature": FIXTURE_PRIVATE_KEY.sign(proof.signing_bytes("calendar.apply")).hex()}
    )


def make_events(clock: FakeClock, count: int) -> tuple[CalendarEventDraft, ...]:
    return tuple(
        CalendarEventDraft(
            event_key=f"event-{index}",
            summary=f"Event {index}",
            starts_at=clock.now() + timedelta(hours=index + 1),
            ends_at=clock.now() + timedelta(hours=index + 2),
        )
        for index in range(count)
    )


def provider_event_id(event_key: str) -> str:
    """Return the deterministic provider event id bound to the adapter owner."""
    digest = hashlib.sha256(f"lifeos-user\x1f{event_key}".encode()).digest()
    return "lifeos" + base64.b32hexencode(digest).decode().lower().rstrip("=")


def seed_approved_proposal(
    store: EncryptedStateStore,
    clock: FakeClock,
    events: tuple[CalendarEventDraft, ...],
) -> ProposalRecord:
    proposal_id = RecordId(uuid4())
    payload = calendar_payload(events)
    store.create(
        Proposal(
            record_id=proposal_id,
            created_at=clock.now(),
            proposal_type="calendar.apply",
            payload=payload,
            state="approved",
            policy_decision="approved",
            provider_version="fake-v1",
            reversible=True,
            approval_state="approved",
        ),
        TransitionContext(
            actor=ACTOR,
            correlation_id=CORRELATION,
            occurred_at=clock.now(),
        ),
    )
    return ProposalRecord(
        proposal_id=proposal_id,
        action_class="calendar.apply",
        payload=payload,
        state=ProposalState.APPROVED,
        created_at=clock.now(),
    )


def enroll_device(store: EncryptedStateStore, clock: FakeClock) -> DeviceRegistry:
    devices = DeviceRegistry(clock, store.devices)
    _ = devices.enroll(
        DEFAULT_DEVICE_ID, "fingerprint", ACTOR, approval_public_key=FIXTURE_PUBLIC_KEY
    )
    return devices


def test_interrupted_write_resets_then_reapproval_creates_no_duplicates(
    store: EncryptedStateStore, clock: FakeClock
) -> None:
    events = make_events(clock, 2)
    proposal = seed_approved_proposal(store, clock, events)
    adapter, sandbox = make_adapter(clock)
    devices = enroll_device(store, clock)
    original = make_approval(clock, proposal, idempotency_key="original")
    lifecycle = ProposalLifecycle(clock, devices, store.authority_consumption)
    _ = lifecycle.approve(
        proposal.model_copy(update={"state": ProposalState.PROPOSED}),
        original,
        default_approval_matrix(),
    )
    reset_request = make_reset(clock, proposal, idempotency_key="reset-1")

    # Interrupted before any provider write: the sandbox has no proposed events.
    outcome = recover_interrupted_apply(store, proposal.proposal_id, adapter, reset_request, clock)

    assert outcome.outcome == "reset"
    assert outcome.state == ProposalState.PROPOSED
    assert outcome.succeeded_events == ()
    assert set(outcome.missing_events) == {provider_event_id(event.event_key) for event in events}
    assert sandbox.proposed_events == ()

    # Re-approval with a fresh proof and apply creates exactly the plan events.
    proposed = proposal.model_copy(update={"state": ProposalState.PROPOSED})
    approval = make_approval(clock, proposed, idempotency_key="approval-2")
    lifecycle = ProposalLifecycle(clock, devices, store.authority_consumption)
    _ = lifecycle.approve(proposed, approval, default_approval_matrix())
    store.transition(
        RecordKind.PROPOSAL,
        proposal.proposal_id,
        "approved",
        TransitionContext(actor=ACTOR, correlation_id=CORRELATION, occurred_at=clock.now()),
    )
    plan = CalendarPlan(
        authorization=proposal.model_copy(update={"state": ProposalState.APPROVED}),
        events=events,
    )
    with pytest.raises(PolicyViolationError, match="replay"):
        _ = lifecycle.approve(proposed, original, default_approval_matrix())
    result = adapter.apply(plan)

    assert len(sandbox.proposed_events) == len(events)
    assert result.operations[0].operation == CalendarOperation.INSERT


def test_partial_write_requires_cleanup_before_reset(
    store: EncryptedStateStore, clock: FakeClock
) -> None:
    events = make_events(clock, 2)
    proposal = seed_approved_proposal(store, clock, events)
    adapter, sandbox = make_adapter(clock)
    _ = enroll_device(store, clock)
    reset_request = make_reset(clock, proposal, idempotency_key="reset-1")

    # Simulate a partial write: the first event commits, the response is lost.
    plan = CalendarPlan(authorization=proposal, events=events)
    sandbox.interrupt_next_write_after_commit()
    with pytest.raises(ConnectionError, match="interrupted"):
        _ = adapter.apply(plan)
    assert len(sandbox.proposed_events) == 1

    # First recovery: partial — the proposal stays APPROVED and records successes.
    outcome = recover_interrupted_apply(store, proposal.proposal_id, adapter, reset_request, clock)
    assert outcome.outcome == "partial"
    assert outcome.state == ProposalState.APPROVED
    assert len(outcome.succeeded_events) == 1
    assert len(outcome.missing_events) == 1

    # Partial effects are durably recorded, without fabricating APPLIED to delete them.
    evidence = [item for item in store.records() if item.kind == RecordKind.EXECUTION]
    assert len(evidence) == 1
    assert len(sandbox.proposed_events) == 1


def test_successful_write_reconciles_to_applied_without_reset(
    store: EncryptedStateStore, clock: FakeClock
) -> None:
    events = make_events(clock, 2)
    proposal = seed_approved_proposal(store, clock, events)
    adapter, sandbox = make_adapter(clock)
    _ = enroll_device(store, clock)
    reset_request = make_reset(clock, proposal, idempotency_key="reset-1")

    plan = CalendarPlan(authorization=proposal, events=events)
    _ = adapter.apply(plan)
    assert len(sandbox.proposed_events) == len(events)

    outcome = recover_interrupted_apply(store, proposal.proposal_id, adapter, reset_request, clock)

    assert outcome.outcome == "applied"
    assert outcome.state == ProposalState.APPLIED
    assert set(outcome.succeeded_events) == {provider_event_id(event.event_key) for event in events}
    assert outcome.missing_events == ()
    stored = store.read(RecordKind.PROPOSAL, proposal.proposal_id)
    assert stored is not None
    assert stored.state == "applied"


def test_lease_replay_is_rejected_after_reset(store: EncryptedStateStore, clock: FakeClock) -> None:
    events = make_events(clock, 1)
    proposal = seed_approved_proposal(store, clock, events)
    adapter, _ = make_adapter(clock)
    devices = enroll_device(store, clock)
    reset_request = make_reset(clock, proposal, idempotency_key="reset-1")

    # The original apply lease was consumed before the interrupted write.
    lease = make_lease(clock, proposal, "calendar.apply", "worker-1")
    lifecycle = ProposalLifecycle(clock, devices, store.authority_consumption)
    _ = lifecycle.apply(proposal, lease)

    # Interrupted before any provider write → reconciliation confirms zero effects → reset.
    outcome = recover_interrupted_apply(store, proposal.proposal_id, adapter, reset_request, clock)
    assert outcome.outcome == "reset"

    # The stored proposal is PROPOSED, so apply is rejected by state.
    stored = store.read(RecordKind.PROPOSAL, proposal.proposal_id)
    assert stored is not None
    assert stored.state == "proposed"
    with pytest.raises(PolicyViolationError, match="cannot apply"):
        _ = lifecycle.apply(proposal.model_copy(update={"state": ProposalState.PROPOSED}), lease)

    # Even a stale APPROVED view cannot replay the consumed lease.
    with pytest.raises(PolicyViolationError, match="replay"):
        _ = lifecycle.apply(proposal, lease)


def test_ordinary_approval_cannot_authorize_reset(
    store: EncryptedStateStore, clock: FakeClock
) -> None:
    events = make_events(clock, 1)
    proposal = seed_approved_proposal(store, clock, events)
    adapter, _ = make_adapter(clock)
    _ = enroll_device(store, clock)
    ordinary = make_approval(clock, proposal)
    # Parsing the ordinary wire shape as a reset cannot change its signed purpose.
    parsed = ResetRequest.model_validate(ordinary.model_dump())
    with pytest.raises(PolicyViolationError, match="signature"):
        _ = recover_interrupted_apply(store, proposal.proposal_id, adapter, parsed, clock)
    stored = store.read(RecordKind.PROPOSAL, proposal.proposal_id)
    assert stored is not None
    assert stored.state == "approved"


def test_external_edit_is_never_reported_as_applied(
    store: EncryptedStateStore, clock: FakeClock
) -> None:
    events = make_events(clock, 1)
    proposal = seed_approved_proposal(store, clock, events)
    adapter, sandbox = make_adapter(clock)
    _ = enroll_device(store, clock)
    _ = adapter.apply(CalendarPlan(authorization=proposal, events=events))
    sandbox.external_edit(sandbox.proposed_events[0].event_id, summary="User correction")
    outcome = recover_interrupted_apply(
        store,
        proposal.proposal_id,
        adapter,
        make_reset(clock, proposal, idempotency_key="reset"),
        clock,
    )
    assert outcome.outcome == "conflict"
    assert outcome.state == ProposalState.APPROVED
    assert sandbox.proposed_events[0].summary == "User correction"


def test_lifecycle_reset_requires_reconciliation_and_cannot_approve(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    proposal = seed_approved_proposal(store, clock, make_events(clock, 1))
    lifecycle = ProposalLifecycle(clock, enroll_device(store, clock), store.authority_consumption)
    proof = make_reset(clock, proposal, idempotency_key="reset")
    with pytest.raises(PolicyViolationError, match="reconciliation"):
        _ = lifecycle.reset(proposal, proof, default_approval_matrix(), zero_effects_verified=False)
    with pytest.raises(PolicyViolationError, match="cannot approve"):
        _ = lifecycle.approve(
            proposal.model_copy(update={"state": ProposalState.PROPOSED}),
            proof,
            default_approval_matrix(),
        )
