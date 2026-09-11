"""Sandbox-only staged rollout controls with encrypted audit evidence."""

from typing import final

from secretary_service.authority import (
    ApprovalMatrix,
    ApprovalProof,
    ApprovalRule,
    AuthorityTier,
    CapabilityLease,
    ProposalLifecycle,
    ProposalRecord,
    ProposalState,
    Rollback,
)
from secretary_service.models import Capability, RecordKind, TransitionContext
from secretary_service.openclaw.contracts import (
    ALLOWED_CAPABILITIES,
    ALLOWED_ENDPOINTS,
    assess_revision,
)
from secretary_service.rollout_models import (
    ROLLOUT_CAPABILITY,
    ROLLOUT_WORKER,
    RolloutApprovalRequest,
    RolloutDependencies,
    RolloutPlan,
    RolloutPreview,
    RolloutResult,
    RolloutTarget,
    RolloutViolationError,
)

__all__ = [
    "RolloutApprovalRequest",
    "RolloutCoordinator",
    "RolloutDependencies",
    "RolloutPlan",
    "RolloutPreview",
    "RolloutResult",
    "RolloutTarget",
    "RolloutViolationError",
]

ROLLOUT_APPROVAL_MATRIX = ApprovalMatrix(
    rules=(
        ApprovalRule(
            action_class="rollout.apply",
            required_tier=AuthorityTier.ACT,
            proof_required=True,
            allowed_proofs=(ApprovalProof.DEVICE_SIGNED,),
        ),
    )
)


@final
class RolloutCoordinator:
    """Stage compatible updates without ever promoting production capability."""

    def __init__(self, dependencies: RolloutDependencies) -> None:
        """Bind encrypted state and payload-bound authority seams."""
        self._dependencies = dependencies
        self._lifecycle = ProposalLifecycle(
            dependencies.clock,
            dependencies.devices,
            dependencies.store.authority_consumption,
        )

    def dry_run(self, plan: RolloutPlan) -> RolloutPreview:
        """Assess a pinned update without changing state or contacting upstream."""
        compatibility = assess_revision(plan.candidate_revision)
        return RolloutPreview(
            rollout_id=plan.rollout_id,
            target=plan.target,
            candidate_revision=plan.candidate_revision,
            revision_compatible=(
                compatibility.compatible and plan.pinned_revision == compatibility.expected_revision
            ),
            endpoints_unchanged=set(plan.expected_endpoints)
            == {endpoint.value for endpoint in ALLOWED_ENDPOINTS},
            capabilities_unchanged=set(plan.expected_capabilities)
            == {capability.value for capability in ALLOWED_CAPABILITIES},
            reversible=plan.reversible,
            fabric_enabled=plan.fabric_enabled,
            workers_enabled=plan.workers_enabled,
            production_capabilities_enabled=plan.production_capabilities_enabled,
            external_operations=0,
        )

    def propose(self, preview: RolloutPreview, context: TransitionContext) -> ProposalRecord:
        """Persist a proposal only for a compatible reversible sandbox plan."""
        self._require_safe_preview(preview)
        self._dependencies.store.create(
            Capability(
                record_id=preview.rollout_id,
                connector_id=preview.rollout_id,
                name="integration-fabric-and-workers",
                state="proposed",
                granted=False,
                created_at=self._dependencies.clock.now(),
            ),
            context,
        )
        return ProposalRecord(
            proposal_id=preview.rollout_id,
            action_class="rollout.apply",
            payload=preview.canonical_payload(),
            state=ProposalState.PROPOSED,
            created_at=self._dependencies.clock.now(),
        )

    def approve(
        self,
        proposal: ProposalRecord,
        request: RolloutApprovalRequest,
    ) -> ProposalRecord:
        """Require an enrolled device proof before marking the stage approved."""
        approval = request.approval
        device_id = approval.device_id
        if device_id is None:
            reason = "device-signed rollout approval required"
            raise RolloutViolationError(reason)
        _ = self._dependencies.devices.verify_mtls_identity(device_id, request.fingerprint)
        state = self._lifecycle.approve(proposal, approval, ROLLOUT_APPROVAL_MATRIX)
        self._transition(proposal, state, request.context)
        return proposal.model_copy(update={"state": state})

    def apply(
        self,
        proposal: ProposalRecord,
        lease: CapabilityLease,
        context: TransitionContext,
    ) -> RolloutResult:
        """Apply only the inert sandbox stage under an exact one-shot lease."""
        preview = RolloutPreview.model_validate_json(proposal.payload)
        self._require_safe_preview(preview)
        if lease.capability != ROLLOUT_CAPABILITY or lease.worker_id != ROLLOUT_WORKER:
            reason = "rollout lease scope mismatch"
            raise RolloutViolationError(reason)
        _ = self._dependencies.leases.verify(lease, proposal)
        state = self._lifecycle.apply(proposal, lease)
        self._transition(proposal, state, context)
        return self._result(proposal.model_copy(update={"state": state}))

    def rollback(
        self,
        proposal: ProposalRecord,
        rollback: Rollback,
        context: TransitionContext,
    ) -> RolloutResult:
        """Downgrade the stage while retaining every encrypted audit entry."""
        state = self._lifecycle.revert(proposal, rollback)
        self._transition(proposal, state, context)
        return self._result(proposal.model_copy(update={"state": state}))

    def _require_safe_preview(self, preview: RolloutPreview) -> None:
        safe = (
            preview.target is RolloutTarget.SANDBOX
            and preview.revision_compatible
            and preview.endpoints_unchanged
            and preview.capabilities_unchanged
            and preview.reversible
            and not preview.fabric_enabled
            and not preview.workers_enabled
            and not preview.production_capabilities_enabled
            and preview.external_operations == 0
        )
        if not safe:
            reason = "rollout is not an inert reversible sandbox stage"
            raise RolloutViolationError(reason)

    def _transition(
        self,
        proposal: ProposalRecord,
        state: ProposalState,
        context: TransitionContext,
    ) -> None:
        self._dependencies.store.transition(
            RecordKind.CAPABILITY,
            proposal.proposal_id,
            state.value,
            context,
        )

    def _result(self, proposal: ProposalRecord) -> RolloutResult:
        return RolloutResult(
            proposal=proposal,
            fabric_enabled=False,
            workers_enabled=False,
            production_capabilities_enabled=False,
            audit_entries=len(self._dependencies.store.audit_entries()),
        )
