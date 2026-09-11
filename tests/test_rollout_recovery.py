import os
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest

from secretary_service.authority import Approval, ApprovalProof, ProposalState, Rollback
from secretary_service.enrollment import DeviceId, DeviceRegistry
from secretary_service.fixture_authority import FIXTURE_PUBLIC_KEY, sign_fixture_approval
from secretary_service.keys import DeterministicTestKeyProvider
from secretary_service.leases import LeaseIssuer, LeaseRequest, SigningKeyRing
from secretary_service.models import ActorId, Capability, CorrelationId, RecordId, RecordKind
from secretary_service.rollout import (
    RolloutApprovalRequest,
    RolloutCoordinator,
    RolloutDependencies,
    RolloutPlan,
    RolloutTarget,
    RolloutViolationError,
)
from secretary_service.storage import EncryptedStateStore
from tests.helpers import FakeClock
from tests.rollout_helpers import (
    golden_projection,
    load_state_fixture,
    seed_foundation,
    transition_context,
)

ROOT = Path(__file__).parents[1]


def fixture_path(environment_name: str, default: str) -> Path:
    return Path(os.environ.get(environment_name, ROOT / default))


def migrate_fixture(path: Path, database_path: Path, *, reopen: bool) -> str:
    clock = FakeClock.from_isoformat("2026-09-07T12:00:00+00:00")
    keys = DeterministicTestKeyProvider.from_seed(b"task-18-state")
    fixture = load_state_fixture(path)
    if reopen:
        with EncryptedStateStore.open(database_path, keys, clock) as store:
            manifest = seed_foundation(store, fixture, clock)
        with EncryptedStateStore.open(database_path, keys, clock) as store:
            _ = store.migrate_canonical(
                manifest,
                transition_context(clock, "task-18-migrate"),
            )
            assert store.verify_audit_chain().entries_verified == 7
            return golden_projection(store.canonical_records())
    with EncryptedStateStore.open(database_path, keys, clock) as store:
        manifest = seed_foundation(store, fixture, clock)
        _ = store.migrate_canonical(
            manifest,
            transition_context(clock, "task-18-migrate"),
        )
        assert store.verify_audit_chain().entries_verified == 7
        return golden_projection(store.canonical_records())


def test_fresh_and_migrated_deployments_match_golden(tmp_path: Path) -> None:
    fresh = fixture_path("LIFEOS_FRESH_FIXTURE", "fixtures/fresh-state")
    migrated = fixture_path("LIFEOS_MIGRATED_FIXTURE", "fixtures/secretary-v2")
    expected = (fresh / "golden.json").read_text(encoding="utf-8")

    fresh_result = migrate_fixture(fresh, tmp_path / "fresh.sqlite", reopen=False)
    migrated_result = migrate_fixture(migrated, tmp_path / "migrated.sqlite", reopen=True)

    assert fresh_result == migrated_result == expected


def test_rollout_is_approved_reversible_and_never_promotes_production(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    plan_path = fixture_path("LIFEOS_OPENCLAW_FIXTURE", "fixtures/openclaw")
    plan = RolloutPlan.model_validate_json(
        (plan_path / "upstream-update.json").read_text(encoding="utf-8")
    )
    devices = DeviceRegistry(clock)
    owner_id = DeviceId("task-18-owner")
    _ = devices.enroll(
        owner_id,
        "task-18-owner-fingerprint",
        ActorId("owner"),
        approval_public_key=FIXTURE_PUBLIC_KEY,
    )
    leases = LeaseIssuer(clock, SigningKeyRing(b"task-18-rollout-signing"), timedelta(minutes=5))
    coordinator = RolloutCoordinator(
        RolloutDependencies(store=store, devices=devices, clock=clock, leases=leases)
    )
    context = transition_context(clock, "task-18-rollout")

    preview = coordinator.dry_run(plan)
    proposal = coordinator.propose(preview, context)
    approval = Approval(
        fact_id=RecordId(UUID("18000000-0000-0000-0000-000000000003")),
        proposal_id=proposal.proposal_id,
        payload_hash=proposal.payload_hash(),
        issued_at=clock.now(),
        expires_at=clock.now() + timedelta(minutes=5),
        idempotency_key="task-18-approval",
        actor=ActorId("owner"),
        correlation_id=CorrelationId("task-18-rollout"),
        proof=ApprovalProof.DEVICE_SIGNED,
        device_id=owner_id,
    )
    approved = coordinator.approve(
        proposal,
        RolloutApprovalRequest(
            approval=sign_fixture_approval(approval, "rollout.apply"),
            fingerprint="task-18-owner-fingerprint",
            context=context,
        ),
    )
    lease = leases.issue(
        approved,
        LeaseRequest(
            capability="rollout.stage",
            worker_id="lifeos-rollout",
            actor=ActorId("owner"),
            correlation_id=CorrelationId("task-18-rollout"),
            idempotency_key="task-18-apply",
        ),
    )
    applied = coordinator.apply(approved, lease, context)
    rollback = Rollback(
        fact_id=RecordId(UUID("18000000-0000-0000-0000-000000000004")),
        proposal_id=applied.proposal.proposal_id,
        payload_hash=applied.proposal.payload_hash(),
        issued_at=clock.now(),
        expires_at=clock.now() + timedelta(minutes=5),
        idempotency_key="task-18-rollback",
        actor=ActorId("owner"),
        correlation_id=CorrelationId("task-18-rollout"),
        reason="downgrade drill",
    )

    rolled_back = coordinator.rollback(applied.proposal, rollback, context)

    assert proposal.state is ProposalState.PROPOSED
    assert approved.state is ProposalState.APPROVED
    assert applied.proposal.state is ProposalState.APPLIED
    assert rolled_back.proposal.state is ProposalState.REVERTED
    assert rolled_back.fabric_enabled is False
    assert rolled_back.workers_enabled is False
    assert rolled_back.production_capabilities_enabled is False
    assert rolled_back.audit_entries == 4
    assert store.verify_audit_chain().entries_verified == 4
    capability = store.read(RecordKind.CAPABILITY, proposal.proposal_id)
    assert isinstance(capability, Capability)
    assert capability.granted is False
    assert capability.state == ProposalState.REVERTED.value


def test_production_rollout_cannot_be_proposed(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    plan_path = fixture_path("LIFEOS_OPENCLAW_FIXTURE", "fixtures/openclaw")
    sandbox = RolloutPlan.model_validate_json(
        (plan_path / "upstream-update.json").read_text(encoding="utf-8")
    )
    production = sandbox.model_copy(update={"target": RolloutTarget.PRODUCTION})
    leases = LeaseIssuer(clock, SigningKeyRing(b"task-18-production-denial"), timedelta(minutes=5))
    coordinator = RolloutCoordinator(
        RolloutDependencies(
            store=store,
            devices=DeviceRegistry(clock),
            clock=clock,
            leases=leases,
        )
    )

    with pytest.raises(RolloutViolationError):
        _ = coordinator.propose(
            coordinator.dry_run(production),
            transition_context(clock, "task-18-production-denial"),
        )

    assert store.audit_entries() == ()
