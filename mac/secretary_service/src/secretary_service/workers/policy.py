"""Manual, Copilot, and narrowly governed Autopilot policy profiles."""

from enum import StrEnum
from typing import ClassVar, final

from pydantic import ConfigDict

from secretary_service.authority import (
    CapabilityLease,
    ProposalRecord,
    ProposalState,
    Reconciliation,
    Rollback,
)
from secretary_service.learning import ProposalStatus, SkillProposal
from secretary_service.leases import LeaseIssuer, LeaseViolationError
from secretary_service.models import FrozenModel, NonEmpty
from secretary_service.storage import Clock

LIFEOS_CALENDAR_WORKER = "lifeos-calendar"


class AuthorityMode(StrEnum):
    """User-selectable execution profiles."""

    MANUAL = "manual"
    COPILOT = "copilot"
    AUTOPILOT = "autopilot"


class AuthorityAction(StrEnum):
    """Closed action families evaluated by authority profiles."""

    CALENDAR_RULE_APPLY = "calendar.rule.apply"
    SPEND = "money.spend"
    DELETE = "external.delete"
    SEND = "message.send"
    BROWSER = "browser.execute"
    COMPUTER = "computer.execute"
    DOCUMENT = "document.execute"


class AuthorityDecision(FrozenModel):
    """Machine-readable profile decision."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    allowed: bool
    reason: NonEmpty
    may_propose: bool
    requires_confirmation: bool


class CalendarRuleAuthorization(FrozenModel):
    """Approved rule plus exact execution, rollback, and reconciliation facts."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    rule: SkillProposal
    proposal: ProposalRecord
    lease: CapabilityLease
    rollback: Rollback | None
    reconciliation: Reconciliation | None


@final
class AuthorityProfilePolicy:
    """Fail closed except for verified reversible LifeOS calendar rules."""

    def __init__(self, clock: Clock, lease_issuer: LeaseIssuer | None = None) -> None:
        """Use the shared clock and optional one-shot lease verifier."""
        self._clock = clock
        self._lease_issuer = lease_issuer

    def authorize(
        self,
        mode: AuthorityMode,
        action: AuthorityAction,
        authorization: CalendarRuleAuthorization | None = None,
    ) -> AuthorityDecision:
        """Authorize an action according to the selected profile."""
        match mode:  # noqa: MATCH_OK - enum is already exhaustive
            case AuthorityMode.MANUAL:
                return AuthorityDecision(
                    allowed=False,
                    reason="manual_confirmation_required",
                    may_propose=True,
                    requires_confirmation=True,
                )
            case AuthorityMode.COPILOT:
                return AuthorityDecision(
                    allowed=False,
                    reason="copilot_proposal_only",
                    may_propose=True,
                    requires_confirmation=False,
                )
            case AuthorityMode.AUTOPILOT:
                return self._authorize_autopilot(action, authorization)

    def _authorize_autopilot(
        self,
        action: AuthorityAction,
        authorization: CalendarRuleAuthorization | None,
    ) -> AuthorityDecision:
        if action is not AuthorityAction.CALENDAR_RULE_APPLY:
            return self._denied("autopilot_action_denied")
        if authorization is None:
            return self._denied("calendar_rule_authorization_required")
        denial = self._calendar_denial(authorization)
        if denial is not None:
            return self._denied(denial)
        if self._lease_issuer is None:
            return self._denied("capability_lease_verifier_unavailable")
        try:
            _ = self._lease_issuer.verify(authorization.lease, authorization.proposal)
        except LeaseViolationError as error:
            return self._denied(str(error))
        return AuthorityDecision(
            allowed=True,
            reason="approved_reversible_calendar_rule",
            may_propose=True,
            requires_confirmation=False,
        )

    def _calendar_denial(self, authorization: CalendarRuleAuthorization) -> str | None:
        rule = authorization.rule
        proposal = authorization.proposal
        lease = authorization.lease
        initial_checks = (
            (rule.status is not ProposalStatus.APPROVED, "approved_learning_rule_required"),
            (
                rule.trigger.kind != "calendar_rule" or rule.trigger.source != "lifeos.calendar",
                "lifeos_calendar_rule_required",
            ),
            (
                proposal.state is not ProposalState.APPROVED
                or proposal.action_class != "calendar.apply",
                "calendar_apply_proposal_required",
            ),
            (
                lease.capability != "calendar.apply" or lease.worker_id != LIFEOS_CALENDAR_WORKER,
                "calendar_capability_lease_required",
            ),
        )
        denial = next((reason for failed, reason in initial_checks if failed), None)
        if denial is not None:
            return denial
        rollback = authorization.rollback
        if rollback is None:
            return "rollback_required"
        reconciliation = authorization.reconciliation
        if reconciliation is None:
            return "reconciliation_required"
        expected = (proposal.proposal_id, proposal.payload_hash())
        now = self._clock.now()
        final_checks = (
            (
                (rollback.proposal_id, rollback.payload_hash) != expected,
                "rollback_binding_mismatch",
            ),
            (
                (reconciliation.proposal_id, reconciliation.payload_hash) != expected,
                "reconciliation_binding_mismatch",
            ),
            (rollback.expires_at <= now, "rollback_expired"),
            (reconciliation.expires_at <= now, "reconciliation_expired"),
        )
        return next((reason for failed, reason in final_checks if failed), None)

    def _denied(self, reason: str) -> AuthorityDecision:
        return AuthorityDecision(
            allowed=False,
            reason=reason,
            may_propose=True,
            requires_confirmation=False,
        )
