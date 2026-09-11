"""Authority tiers, proposal lifecycle, rollback, and capability leases."""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from secretary_service.authority import (
    Approval,
    ApprovalMatrix,
    ApprovalProof,
    ApprovalRule,
    AuthorityTier,
    CapabilityLease,
    InvocationMethod,
    PolicyViolationError,
    ProposalLifecycle,
    ProposalRecord,
    ProposalState,
    Reconciliation,
    Rollback,
    default_approval_matrix,
)
from secretary_service.enrollment import DeviceId, DeviceRegistry
from secretary_service.fixture_authority import FIXTURE_PUBLIC_KEY, sign_fixture_approval
from secretary_service.leases import LeaseIssuer, LeaseRequest, LeaseViolationError, SigningKeyRing
from secretary_service.models import (
    ActorId,
    CorrelationId,
    Proposal,
    RecordId,
    RecordKind,
    TransitionContext,
)
from secretary_service.storage import EncryptedStateStore
from tests.helpers import FakeClock

ACTOR = ActorId("user")
CORRELATION = CorrelationId("corr-authority")
DEFAULT_DEVICE_ID = DeviceId("iphone-1")
DEFAULT_APPROVAL_TTL = timedelta(minutes=5)
DEFAULT_LEASE_TTL = timedelta(minutes=10)


def make_proposal(
    clock: FakeClock,
    action_class: str,
    payload: str,
    *,
    state: ProposalState = ProposalState.PROPOSED,
) -> ProposalRecord:
    return ProposalRecord(
        proposal_id=RecordId(uuid4()),
        action_class=action_class,
        payload=payload,
        state=state,
        created_at=clock.now(),
    )


def make_approval(  # noqa: PLR0913
    clock: FakeClock,
    proposal: ProposalRecord,
    *,
    proof: ApprovalProof = ApprovalProof.DEVICE_SIGNED,
    device_id: DeviceId | None = DEFAULT_DEVICE_ID,
    payload_hash: str | None = None,
    expires_in: timedelta = DEFAULT_APPROVAL_TTL,
    idempotency_key: str = "approval-1",
) -> Approval:
    approval = Approval(
        fact_id=RecordId(uuid4()),
        proposal_id=proposal.proposal_id,
        payload_hash=proposal.payload_hash() if payload_hash is None else payload_hash,
        proof=proof,
        device_id=device_id,
        issued_at=clock.now(),
        expires_at=clock.now() + expires_in,
        idempotency_key=idempotency_key,
        actor=ACTOR,
        correlation_id=CORRELATION,
    )

    return sign_fixture_approval(approval, proposal.action_class)


def make_lease(  # noqa: PLR0913
    clock: FakeClock,
    proposal: ProposalRecord,
    capability: str,
    worker_id: str,
    *,
    expires_in: timedelta = DEFAULT_LEASE_TTL,
    idempotency_key: str = "lease-1",
) -> CapabilityLease:
    return CapabilityLease(
        fact_id=RecordId(uuid4()),
        proposal_id=proposal.proposal_id,
        payload_hash=proposal.payload_hash(),
        capability=capability,
        worker_id=worker_id,
        issued_at=clock.now(),
        expires_at=clock.now() + expires_in,
        idempotency_key=idempotency_key,
        actor=ACTOR,
        correlation_id=CORRELATION,
        signature="0" * 64,
    )


def make_lease_request(capability: str, worker_id: str) -> LeaseRequest:
    return LeaseRequest(
        capability=capability,
        worker_id=worker_id,
        actor=ACTOR,
        correlation_id=CORRELATION,
        idempotency_key="lease-1",
    )


def make_lifecycle(clock: FakeClock) -> ProposalLifecycle:
    """Create a lifecycle with the test iPhone enrolled and active."""
    devices = DeviceRegistry(clock)
    _ = devices.enroll(
        DEFAULT_DEVICE_ID, "test-iphone-1", ACTOR, approval_public_key=FIXTURE_PUBLIC_KEY
    )
    return ProposalLifecycle(clock, devices)


def approve_proposal(
    clock: FakeClock,
    lifecycle: ProposalLifecycle,
    matrix: ApprovalMatrix,
    proposal: ProposalRecord,
) -> ProposalRecord:
    approval = make_approval(clock, proposal)
    assert lifecycle.approve(proposal, approval, matrix) == ProposalState.APPROVED
    return proposal.model_copy(update={"state": ProposalState.APPROVED})


def test_baseline_persistence_accepts_unassessed_policy_and_records_actor(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    # Given: the Todo 2 persistence foundation, which stores proposals without
    # authority enforcement.
    proposal_id = RecordId(UUID("00000000-0000-0000-0000-000000000201"))
    store.create(
        Proposal(
            record_id=proposal_id,
            created_at=clock.now(),
            proposal_type="calendar.apply",
            payload="move workout to 6pm",
            state="proposed",
            policy_decision="unassessed",
            provider_version="fake-v1",
            reversible=True,
            approval_state="pending",
        ),
        TransitionContext(
            actor=ActorId("provider:fake"),
            correlation_id=CorrelationId("corr-baseline"),
            occurred_at=clock.now(),
        ),
    )
    # When: the persisted proposal and its audit entry are inspected.
    persisted = store.read(RecordKind.PROPOSAL, proposal_id)
    entries = store.audit_entries()
    # Then: the record persists with unenforced policy fields and the audit
    # entry records the actor and correlation id (Todo 3 adds enforcement).
    assert persisted is not None
    assert isinstance(persisted, Proposal)
    assert persisted.policy_decision == "unassessed"
    assert persisted.approval_state == "pending"
    assert entries[-1].actor == ActorId("provider:fake")
    assert entries[-1].correlation_id == CorrelationId("corr-baseline")


def test_voice_can_request_but_never_approve_consequential_action() -> None:
    matrix = default_approval_matrix()
    request = matrix.can_request("calendar.apply", InvocationMethod.VOICE)
    assert request.allowed
    approval = matrix.can_approve("calendar.apply", ApprovalProof.VOICE)
    assert not approval.allowed
    assert "voice" in approval.reason


def test_channel_source_cannot_elevate_authority() -> None:
    matrix = default_approval_matrix()
    for method in (InvocationMethod.CHANNEL, InvocationMethod.WORKER, InvocationMethod.API):
        request = matrix.can_request("message.send", method)
        assert request.allowed
    decision = matrix.can_approve("message.send", ApprovalProof.VOICE)
    assert not decision.allowed
    assert "voice" in decision.reason


def test_no_invocation_method_can_elevate_required_tier() -> None:
    matrix = default_approval_matrix()
    for rule in matrix.rules:
        for method in InvocationMethod:
            decision = matrix.can_request(rule.action_class, method)
            assert decision.allowed
            assert decision.required_tier == rule.required_tier
        if rule.proof_required:
            assert ApprovalProof.VOICE not in rule.allowed_proofs


def test_unknown_action_class_is_denied() -> None:
    matrix = default_approval_matrix()
    decision = matrix.can_approve("money.spend", ApprovalProof.DEVICE_SIGNED)
    assert not decision.allowed
    assert "unknown action" in decision.reason


def test_device_signed_approval_allows_consequential_apply_and_revert(
    clock: FakeClock,
) -> None:
    matrix = default_approval_matrix()
    lifecycle = make_lifecycle(clock)
    ring = SigningKeyRing(b"signing-key-1")
    issuer = LeaseIssuer(clock, ring, timedelta(minutes=10))
    proposal = make_proposal(clock, "calendar.apply", "move workout to 6pm")

    approved = approve_proposal(clock, lifecycle, matrix, proposal)
    lease = issuer.issue(approved, make_lease_request("calendar.apply", "worker-1"))
    verification = issuer.verify(lease, approved)
    assert verification.capability == "calendar.apply"
    assert verification.worker_id == "worker-1"
    assert lifecycle.apply(approved, lease) == ProposalState.APPLIED
    applied = approved.model_copy(update={"state": ProposalState.APPLIED})

    rollback = Rollback(
        fact_id=RecordId(uuid4()),
        proposal_id=applied.proposal_id,
        payload_hash=applied.payload_hash(),
        issued_at=clock.now(),
        expires_at=clock.now() + timedelta(minutes=5),
        idempotency_key="rollback-1",
        actor=ACTOR,
        correlation_id=CORRELATION,
        reason="secretary-owned undo",
    )
    assert lifecycle.revert(applied, rollback) == ProposalState.REVERTED


def test_device_signed_approval_rejects_unknown_or_revoked_device(clock: FakeClock) -> None:
    """Caller-supplied device ids cannot substitute for active enrollment."""
    matrix = default_approval_matrix()
    devices = DeviceRegistry(clock)
    _ = devices.enroll(
        DEFAULT_DEVICE_ID, "test-iphone-1", ACTOR, approval_public_key=FIXTURE_PUBLIC_KEY
    )
    lifecycle = ProposalLifecycle(clock, devices)
    proposal = make_proposal(clock, "calendar.apply", "move workout to 6pm")

    unknown = make_approval(clock, proposal, device_id=DeviceId("forged-iphone"))
    with pytest.raises(PolicyViolationError, match="active enrolled device"):
        _ = lifecycle.approve(proposal, unknown, matrix)

    _ = devices.revoke(DEFAULT_DEVICE_ID, ACTOR)
    revoked = make_approval(clock, proposal)
    with pytest.raises(PolicyViolationError, match="active enrolled device"):
        _ = lifecycle.approve(proposal, revoked, matrix)


def test_approval_for_changed_payload_is_rejected(clock: FakeClock) -> None:
    matrix = default_approval_matrix()
    lifecycle = make_lifecycle(clock)
    proposal = make_proposal(clock, "calendar.apply", "move workout to 6pm")
    approval = make_approval(clock, proposal, payload_hash="0" * 64)
    with pytest.raises(PolicyViolationError, match="payload hash"):
        _ = lifecycle.approve(proposal, approval, matrix)


def test_expired_approval_is_rejected(clock: FakeClock) -> None:
    matrix = default_approval_matrix()
    lifecycle = make_lifecycle(clock)
    proposal = make_proposal(clock, "calendar.apply", "move workout to 6pm")
    approval = make_approval(clock, proposal, expires_in=timedelta(minutes=5))
    clock.advance(timedelta(minutes=5, seconds=1))
    with pytest.raises(PolicyViolationError, match="expired"):
        _ = lifecycle.approve(proposal, approval, matrix)


def test_interrupted_spoken_approval_never_approves(clock: FakeClock) -> None:
    matrix = default_approval_matrix()
    lifecycle = make_lifecycle(clock)
    ring = SigningKeyRing(b"signing-key-1")
    issuer = LeaseIssuer(clock, ring, timedelta(minutes=10))
    proposal = make_proposal(clock, "calendar.apply", "move workout to 6pm")
    # A voice session is interrupted before any device-signed proof exists; the
    # only artifact is a voice "approval" that can never prove identity.
    spoken = make_approval(clock, proposal, proof=ApprovalProof.VOICE, device_id=None)
    with pytest.raises(PolicyViolationError, match="cannot approve"):
        _ = lifecycle.approve(proposal, spoken, matrix)
    # The proposal never reaches APPROVED, so no execution lease can be issued.
    with pytest.raises(LeaseViolationError, match="state"):
        _ = issuer.issue(proposal, make_lease_request("calendar.apply", "worker-1"))


def test_apply_replay_is_rejected(clock: FakeClock) -> None:
    lifecycle = make_lifecycle(clock)
    proposal = make_proposal(
        clock, "calendar.apply", "move workout to 6pm", state=ProposalState.APPROVED
    )
    lease = make_lease(clock, proposal, "calendar.apply", "worker-1")
    assert lifecycle.apply(proposal, lease) == ProposalState.APPLIED
    with pytest.raises(PolicyViolationError, match="replay"):
        _ = lifecycle.apply(proposal, lease)


def test_revert_requires_applied_state(clock: FakeClock) -> None:
    lifecycle = make_lifecycle(clock)
    proposal = make_proposal(clock, "calendar.apply", "move workout to 6pm")
    rollback = Rollback(
        fact_id=RecordId(uuid4()),
        proposal_id=proposal.proposal_id,
        payload_hash=proposal.payload_hash(),
        issued_at=clock.now(),
        expires_at=clock.now() + timedelta(minutes=5),
        idempotency_key="rollback-1",
        actor=ACTOR,
        correlation_id=CORRELATION,
        reason="secretary-owned undo",
    )
    with pytest.raises(PolicyViolationError, match="state"):
        _ = lifecycle.revert(proposal, rollback)


def test_reconcile_records_unknown_outcome_with_payload_binding(clock: FakeClock) -> None:
    lifecycle = make_lifecycle(clock)
    proposal = make_proposal(
        clock, "calendar.apply", "move workout to 6pm", state=ProposalState.APPLIED
    )
    reconciliation = Reconciliation(
        fact_id=RecordId(uuid4()),
        proposal_id=proposal.proposal_id,
        payload_hash=proposal.payload_hash(),
        issued_at=clock.now(),
        expires_at=clock.now() + timedelta(minutes=5),
        idempotency_key="reconcile-1",
        actor=ActorId("executor"),
        correlation_id=CORRELATION,
        outcome="unknown-outcome",
    )
    assert lifecycle.reconcile(proposal, reconciliation) == ProposalState.RECONCILED
    wrong = reconciliation.model_copy(update={"payload_hash": "0" * 64})
    with pytest.raises(PolicyViolationError, match="payload hash"):
        _ = lifecycle.reconcile(proposal, wrong)


def test_payload_bound_facts_require_audit_actor(clock: FakeClock) -> None:
    proposal = make_proposal(clock, "calendar.apply", "move workout to 6pm")
    with pytest.raises(ValidationError, match="at least 1 character"):
        _ = Approval(
            fact_id=RecordId(uuid4()),
            proposal_id=proposal.proposal_id,
            payload_hash=proposal.payload_hash(),
            proof=ApprovalProof.DEVICE_SIGNED,
            device_id=DeviceId("iphone-1"),
            issued_at=clock.now(),
            expires_at=clock.now() + timedelta(minutes=5),
            idempotency_key="approval-1",
            actor=ActorId(""),
            correlation_id=CORRELATION,
        )


def test_lease_replay_is_rejected(clock: FakeClock) -> None:
    ring = SigningKeyRing(b"signing-key-1")
    issuer = LeaseIssuer(clock, ring, timedelta(minutes=10))
    proposal = make_proposal(
        clock, "calendar.apply", "move workout to 6pm", state=ProposalState.APPROVED
    )
    lease = issuer.issue(proposal, make_lease_request("calendar.apply", "worker-1"))
    _ = issuer.verify(lease, proposal)
    with pytest.raises(LeaseViolationError, match="replay"):
        _ = issuer.verify(lease, proposal)


def test_expired_lease_is_rejected(clock: FakeClock) -> None:
    ring = SigningKeyRing(b"signing-key-1")
    issuer = LeaseIssuer(clock, ring, timedelta(minutes=10))
    proposal = make_proposal(
        clock, "calendar.apply", "move workout to 6pm", state=ProposalState.APPROVED
    )
    lease = issuer.issue(proposal, make_lease_request("calendar.apply", "worker-1"))
    clock.advance(timedelta(minutes=10, seconds=1))
    with pytest.raises(LeaseViolationError, match="expired"):
        _ = issuer.verify(lease, proposal)


def test_forged_lease_signature_is_rejected(clock: FakeClock) -> None:
    ring = SigningKeyRing(b"signing-key-1")
    issuer = LeaseIssuer(clock, ring, timedelta(minutes=10))
    proposal = make_proposal(
        clock, "calendar.apply", "move workout to 6pm", state=ProposalState.APPROVED
    )
    lease = issuer.issue(proposal, make_lease_request("calendar.apply", "worker-1"))
    forged = lease.model_copy(update={"signature": "0" * 64})
    with pytest.raises(LeaseViolationError, match="signature"):
        _ = issuer.verify(forged, proposal)


def test_key_rotation_invalidates_old_leases(clock: FakeClock) -> None:
    ring = SigningKeyRing(b"signing-key-1")
    issuer = LeaseIssuer(clock, ring, timedelta(minutes=10))
    proposal = make_proposal(
        clock, "calendar.apply", "move workout to 6pm", state=ProposalState.APPROVED
    )
    old_lease = issuer.issue(proposal, make_lease_request("calendar.apply", "worker-1"))
    _ = issuer.verify(old_lease, proposal)
    ring.rotate(b"signing-key-2")
    new_lease = issuer.issue(proposal, make_lease_request("calendar.apply", "worker-1"))
    _ = issuer.verify(new_lease, proposal)
    with pytest.raises(LeaseViolationError, match="signature"):
        _ = issuer.verify(old_lease, proposal)


def test_lease_requires_approved_proposal(clock: FakeClock) -> None:
    ring = SigningKeyRing(b"signing-key-1")
    issuer = LeaseIssuer(clock, ring, timedelta(minutes=10))
    proposal = make_proposal(clock, "calendar.apply", "move workout to 6pm")
    with pytest.raises(LeaseViolationError, match="state"):
        _ = issuer.issue(proposal, make_lease_request("calendar.apply", "worker-1"))


def test_approval_rule_requires_proof_for_consequential_tiers() -> None:
    matrix = default_approval_matrix()
    for rule in matrix.rules:
        if rule.required_tier.value in ("act", "external"):
            assert rule.proof_required
            assert ApprovalProof.DEVICE_SIGNED in rule.allowed_proofs
        else:
            assert not rule.proof_required


def test_custom_matrix_denies_unknown_proof() -> None:
    matrix = ApprovalMatrix(
        rules=(
            ApprovalRule(
                action_class="calendar.apply",
                required_tier=AuthorityTier.ACT,
                proof_required=True,
                allowed_proofs=(ApprovalProof.DEVICE_SIGNED,),
            ),
        )
    )
    decision = matrix.can_approve("calendar.apply", ApprovalProof.VOICE)
    assert not decision.allowed
    assert "allowed proofs" in decision.reason
