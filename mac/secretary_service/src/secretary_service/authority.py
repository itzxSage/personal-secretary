"""Authority tiers, approval matrix, and payload-bound proposal lifecycle.

Threat model (concise; full version in docs/authority.md):

- T1 invocation-method elevation: a voice/channel/worker source claims a higher
  authority tier than its action rule allows. Mitigation: the required tier is
  derived only from the action class; the invocation method never changes it.
- T2 forged approval: an approval record is fabricated without an enrolled
  device signature. Mitigation: consequential approvals require a device-signed
  proof; voice/channel proofs are never accepted.
- T3 replay: an approval or lease is reused for a different payload or after
  consumption. Mitigation: payload-hash binding plus one-shot consumption.
- T4 expiry bypass: an approval or lease is used after its deadline.
  Mitigation: every transition re-checks expiry against the injected clock.
- T5 interrupted spoken approval: a voice approval that never completes is
  treated as a request, never as proof. Mitigation: proof must be device-signed.
- T6 key rotation: signatures made with a rotated-out signing key are rejected.
  Mitigation: lease verification consults only the current signing key.
- T7 forged/revoked device: an attacker presents an unknown or revoked mTLS
  identity. Mitigation: the device registry verifies fingerprint and active
  state before any approval is attributed to the device.
"""

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Annotated, ClassVar, final, override

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import ConfigDict, StringConstraints

from secretary_service.authority_consumption import ConsumptionStore, MemoryConsumptionStore
from secretary_service.enrollment import (
    DeviceId,
    DeviceNotFoundError,
    DeviceRegistry,
    RevokedDeviceError,
)
from secretary_service.models import (
    ActorId,
    CorrelationId,
    FrozenModel,
    NonEmpty,
    RecordId,
)
from secretary_service.storage import Clock


class AuthorityTier(StrEnum):
    """Closed authority ladder; tier is a property of the action, not the source."""

    OBSERVE = "observe"
    RECOMMEND = "recommend"
    ACT = "act"
    EXTERNAL = "external"


class InvocationMethod(StrEnum):
    """Closed set of surfaces that may request an action."""

    IPHONE_UI = "iphone_ui"
    VOICE = "voice"
    TEXT = "text"
    CHANNEL = "channel"
    WORKER = "worker"
    API = "api"


class ApprovalProof(StrEnum):
    """Closed set of approval proofs; voice is a request, never a proof."""

    NONE = "none"
    DEVICE_SIGNED = "device_signed"
    VOICE = "voice"


class ApprovalRule(FrozenModel):
    """One action class mapped to its required tier and acceptable proofs."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    action_class: NonEmpty
    required_tier: AuthorityTier
    proof_required: bool
    allowed_proofs: tuple[ApprovalProof, ...]


class PolicyDecision(FrozenModel):
    """Outcome of one policy question with the tier and reason attached."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    allowed: bool
    action_class: NonEmpty
    required_tier: AuthorityTier
    reason: NonEmpty


class ApprovalMatrix(FrozenModel):
    """Deterministic action-to-tier mapping with proof allowlists."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    rules: tuple[ApprovalRule, ...]

    def rule_for(self, action_class: str) -> ApprovalRule | None:
        """Return the rule for one action class, if any."""
        return next((rule for rule in self.rules if rule.action_class == action_class), None)

    def required_tier(self, action_class: str) -> AuthorityTier:
        """Return the tier mandated by the action class alone."""
        rule = self.rule_for(action_class)
        if rule is None:
            raise UnknownActionError(action_class)
        return rule.required_tier

    def can_request(self, action_class: str, method: InvocationMethod) -> PolicyDecision:
        """Allow any invocation method to request; authority is enforced later."""
        rule = self.rule_for(action_class)
        if rule is None:
            return PolicyDecision(
                allowed=False,
                action_class=action_class,
                required_tier=AuthorityTier.OBSERVE,
                reason=f"unknown action class: {action_class}",
            )
        return PolicyDecision(
            allowed=True,
            action_class=action_class,
            required_tier=rule.required_tier,
            reason=f"{method.value} may request; approval proof still required",
        )

    def can_approve(self, action_class: str, proof: ApprovalProof) -> PolicyDecision:
        """Accept only proofs listed for the action's tier; never elevate."""
        rule = self.rule_for(action_class)
        if rule is None:
            return PolicyDecision(
                allowed=False,
                action_class=action_class,
                required_tier=AuthorityTier.OBSERVE,
                reason=f"unknown action class: {action_class}",
            )
        if not rule.proof_required:
            return PolicyDecision(
                allowed=True,
                action_class=action_class,
                required_tier=rule.required_tier,
                reason="no approval required for this tier",
            )
        if proof not in rule.allowed_proofs:
            allowed = ", ".join(item.value for item in rule.allowed_proofs) or "none"
            return PolicyDecision(
                allowed=False,
                action_class=action_class,
                required_tier=rule.required_tier,
                reason=(
                    f"proof {proof.value} cannot approve {action_class}; allowed proofs: {allowed}"
                ),
            )
        return PolicyDecision(
            allowed=True,
            action_class=action_class,
            required_tier=rule.required_tier,
            reason=f"proof {proof.value} accepted for {action_class}",
        )


class ProposalState(StrEnum):
    """Closed proposal lifecycle states."""

    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    APPLIED = "applied"
    REVERTED = "reverted"
    RECONCILED = "reconciled"


class ProposalRecord(FrozenModel):
    """Policy-layer proposal with a canonical payload binding."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    proposal_id: RecordId
    action_class: NonEmpty
    payload: NonEmpty
    state: ProposalState
    created_at: datetime

    def payload_hash(self) -> str:
        """Return the canonical SHA-256 binding over the proposal payload."""
        return hashlib.sha256(self.payload.encode("utf-8")).hexdigest()


class PayloadBoundFact(FrozenModel):
    """Authorization fact bound to one proposal payload with expiry and actor."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    fact_id: RecordId
    proposal_id: RecordId
    payload_hash: NonEmpty
    issued_at: datetime
    expires_at: datetime
    idempotency_key: NonEmpty
    actor: Annotated[ActorId, StringConstraints(min_length=1)]
    correlation_id: Annotated[CorrelationId, StringConstraints(min_length=1)]


class Approval(PayloadBoundFact):
    """Device-signed approval; voice proofs never satisfy consequential tiers."""

    proof: ApprovalProof
    device_id: DeviceId | None = None
    signature: str = ""

    def signing_bytes(self, action_class: str) -> bytes:
        """Bind the action and every authorization field with a versioned domain."""
        payload = self.model_dump(mode="json", exclude={"signature"})
        return json.dumps(
            ["lifeos.approval.v1", action_class, payload],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")


class CapabilityLease(PayloadBoundFact):
    """One-shot worker capability lease signed by the current signing key."""

    capability: NonEmpty
    worker_id: NonEmpty
    signature: NonEmpty


class Rollback(PayloadBoundFact):
    """Payload-bound undo fact for an applied proposal."""

    reason: NonEmpty


class Reconciliation(PayloadBoundFact):
    """Payload-bound record of an unknown external execution outcome."""

    outcome: NonEmpty


@final
class PolicyViolationError(Exception):
    """A proposal transition violated the authority policy."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    @override
    def __str__(self) -> str:
        return self.reason


@final
class UnknownActionError(Exception):
    """No approval rule exists for the requested action class."""

    def __init__(self, action_class: str) -> None:
        super().__init__(action_class)
        self.action_class = action_class

    @override
    def __str__(self) -> str:
        return f"unknown action class: {self.action_class}"


@final
class ProposalLifecycle:
    """Deterministic proposal transitions requiring payload-bound authorization."""

    def __init__(
        self, clock: Clock, devices: DeviceRegistry, consumption: ConsumptionStore | None = None
    ) -> None:
        self._clock = clock
        self._devices = devices
        self._consumption = consumption if consumption is not None else MemoryConsumptionStore()

    def approve(
        self,
        proposal: ProposalRecord,
        approval: Approval,
        matrix: ApprovalMatrix,
    ) -> ProposalState:
        """Approve a proposed action only with an accepted, unexpired proof."""
        if proposal.state != ProposalState.PROPOSED:
            message = f"cannot approve proposal in state {proposal.state.value}"
            raise PolicyViolationError(message)
        self.validate_approval(proposal, approval, matrix)
        replay_keys = (
            ("approve", str(approval.fact_id)),
            ("approve-proposal", str(proposal.proposal_id)),
            ("approve-idempotency", f"{approval.device_id}:{approval.idempotency_key}"),
        )
        if not self._consumption.consume(replay_keys):
            message = "approval replay"
            raise PolicyViolationError(message)
        return ProposalState.APPROVED

    def validate_approval(
        self, proposal: ProposalRecord, approval: Approval, matrix: ApprovalMatrix
    ) -> None:
        """Recheck proof, payload, expiry and current device state without consuming it.

        This check alone does not authorize execution: callers also enforce the
        stored proposal state, durable consumption, and the execution lease.
        """
        now = self._clock.now()
        decision = matrix.can_approve(proposal.action_class, approval.proof)
        if not decision.allowed:
            raise PolicyViolationError(decision.reason)
        if approval.proposal_id != proposal.proposal_id:
            message = "approval proposal id does not match"
            raise PolicyViolationError(message)
        if approval.payload_hash != proposal.payload_hash():
            message = "approval payload hash does not match proposal"
            raise PolicyViolationError(message)
        if approval.expires_at <= now:
            message = "approval has expired"
            raise PolicyViolationError(message)
        if approval.issued_at > now or approval.issued_at >= approval.expires_at:
            message = "approval issuance time is invalid"
            raise PolicyViolationError(message)
        if approval.proof == ApprovalProof.DEVICE_SIGNED:
            self._verify_device_signature(proposal, approval)

    def _verify_device_signature(self, proposal: ProposalRecord, approval: Approval) -> None:
        """Verify current enrollment and exact signed approval fields."""
        if approval.device_id is None:
            message = "device-signed approval requires an enrolled device id"
            raise PolicyViolationError(message)
        try:
            device = self._devices.require_active_device(approval.device_id)
        except (DeviceNotFoundError, RevokedDeviceError) as error:
            message = f"device-signed approval requires an active enrolled device: {error}"
            raise PolicyViolationError(message) from error
        if device.approval_public_key is None:
            message = "device has no enrolled approval signing key"
            raise PolicyViolationError(message)
        try:
            key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(device.approval_public_key))
            key.verify(
                bytes.fromhex(approval.signature), approval.signing_bytes(proposal.action_class)
            )
        except (ValueError, InvalidSignature) as error:
            message = "approval signature verification failed"
            raise PolicyViolationError(message) from error

    def apply(self, proposal: ProposalRecord, lease: CapabilityLease) -> ProposalState:
        """Apply an approved proposal under a valid payload-bound lease."""
        now = self._clock.now()
        if proposal.state != ProposalState.APPROVED:
            message = f"cannot apply proposal in state {proposal.state.value}"
            raise PolicyViolationError(message)
        if lease.proposal_id != proposal.proposal_id:
            message = "lease proposal id does not match"
            raise PolicyViolationError(message)
        if lease.payload_hash != proposal.payload_hash():
            message = "lease payload hash does not match proposal"
            raise PolicyViolationError(message)
        if lease.expires_at <= now:
            message = "lease has expired"
            raise PolicyViolationError(message)
        self._consume("apply", str(lease.fact_id))
        return ProposalState.APPLIED

    def revert(self, proposal: ProposalRecord, rollback: Rollback) -> ProposalState:
        """Revert an applied proposal with a payload-bound undo fact."""
        now = self._clock.now()
        if proposal.state != ProposalState.APPLIED:
            message = f"cannot revert proposal in state {proposal.state.value}"
            raise PolicyViolationError(message)
        if rollback.proposal_id != proposal.proposal_id:
            message = "rollback proposal id does not match"
            raise PolicyViolationError(message)
        if rollback.payload_hash != proposal.payload_hash():
            message = "rollback payload hash does not match proposal"
            raise PolicyViolationError(message)
        if rollback.expires_at <= now:
            message = "rollback has expired"
            raise PolicyViolationError(message)
        self._consume("revert", str(rollback.fact_id))
        return ProposalState.REVERTED

    def reconcile(
        self,
        proposal: ProposalRecord,
        reconciliation: Reconciliation,
    ) -> ProposalState:
        """Record an unknown external outcome for an applied proposal."""
        now = self._clock.now()
        if proposal.state != ProposalState.APPLIED:
            message = f"cannot reconcile proposal in state {proposal.state.value}"
            raise PolicyViolationError(message)
        if reconciliation.proposal_id != proposal.proposal_id:
            message = "reconciliation proposal id does not match"
            raise PolicyViolationError(message)
        if reconciliation.payload_hash != proposal.payload_hash():
            message = "reconciliation payload hash does not match proposal"
            raise PolicyViolationError(message)
        if reconciliation.expires_at <= now:
            message = "reconciliation has expired"
            raise PolicyViolationError(message)
        self._consume("reconcile", str(reconciliation.fact_id))
        return ProposalState.RECONCILED

    def _consume(self, transition: str, fact_id: str) -> None:
        key = (transition, fact_id)
        if not self._consumption.consume((key,)):
            message = f"{transition} replay for fact {fact_id}"
            raise PolicyViolationError(message)


def default_approval_matrix() -> ApprovalMatrix:
    """Return the canonical action-to-tier approval matrix."""
    return ApprovalMatrix(
        rules=(
            ApprovalRule(
                action_class="observe.report",
                required_tier=AuthorityTier.OBSERVE,
                proof_required=False,
                allowed_proofs=(),
            ),
            ApprovalRule(
                action_class="plan.recommend",
                required_tier=AuthorityTier.RECOMMEND,
                proof_required=False,
                allowed_proofs=(),
            ),
            ApprovalRule(
                action_class="task.move",
                required_tier=AuthorityTier.ACT,
                proof_required=True,
                allowed_proofs=(ApprovalProof.DEVICE_SIGNED,),
            ),
            ApprovalRule(
                action_class="calendar.apply",
                required_tier=AuthorityTier.ACT,
                proof_required=True,
                allowed_proofs=(ApprovalProof.DEVICE_SIGNED,),
            ),
            ApprovalRule(
                action_class="calendar.revert",
                required_tier=AuthorityTier.ACT,
                proof_required=True,
                allowed_proofs=(ApprovalProof.DEVICE_SIGNED,),
            ),
            ApprovalRule(
                action_class="message.send",
                required_tier=AuthorityTier.EXTERNAL,
                proof_required=True,
                allowed_proofs=(ApprovalProof.DEVICE_SIGNED,),
            ),
            ApprovalRule(
                action_class="worker.execute",
                required_tier=AuthorityTier.EXTERNAL,
                proof_required=True,
                allowed_proofs=(ApprovalProof.DEVICE_SIGNED,),
            ),
        )
    )
