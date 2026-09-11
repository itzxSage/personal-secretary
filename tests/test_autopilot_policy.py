"""Authority profile boundaries for worker and calendar actions."""

from datetime import timedelta
from uuid import uuid4

import pytest

from secretary_service.authority import (
    ProposalRecord,
    ProposalState,
    Reconciliation,
    Rollback,
)
from secretary_service.learning import ObservationTrigger, ProposalStatus, SkillProposal
from secretary_service.leases import LeaseIssuer, LeaseRequest, SigningKeyRing
from secretary_service.models import ActorId, CorrelationId, RecordId
from secretary_service.workers import (
    AuthorityAction,
    AuthorityMode,
    AuthorityProfilePolicy,
    CalendarRuleAuthorization,
)
from tests.helpers import FakeClock

ACTOR = ActorId("user")
CORRELATION = CorrelationId("task-17-policy")
CALENDAR_WORKER = "lifeos-calendar"


def calendar_authorization(
    clock: FakeClock,
    issuer: LeaseIssuer,
) -> CalendarRuleAuthorization:
    proposal = ProposalRecord(
        proposal_id=RecordId(uuid4()),
        action_class="calendar.apply",
        payload='[{"event_key":"work"}]',
        state=ProposalState.APPROVED,
        created_at=clock.now(),
    )
    lease = issuer.issue(
        proposal,
        LeaseRequest(
            capability="calendar.apply",
            worker_id=CALENDAR_WORKER,
            actor=ACTOR,
            correlation_id=CORRELATION,
            idempotency_key="task-17-calendar",
        ),
    )
    return CalendarRuleAuthorization(
        rule=SkillProposal(
            proposal_id=RecordId(uuid4()),
            trigger=ObservationTrigger(kind="calendar_rule", source="lifeos.calendar"),
            procedure=("replan eligible flexible blocks",),
            supporting_observation_ids=(RecordId(uuid4()),),
            confidence=1.0,
            explanation="fixture",
            status=ProposalStatus.APPROVED,
            created_at=clock.now(),
        ),
        proposal=proposal,
        lease=lease,
        rollback=Rollback(
            fact_id=RecordId(uuid4()),
            proposal_id=proposal.proposal_id,
            payload_hash=proposal.payload_hash(),
            issued_at=clock.now(),
            expires_at=clock.now() + timedelta(minutes=4),
            idempotency_key="task-17-rollback",
            actor=ACTOR,
            correlation_id=CORRELATION,
            reason="restore prior owned event projection",
        ),
        reconciliation=Reconciliation(
            fact_id=RecordId(uuid4()),
            proposal_id=proposal.proposal_id,
            payload_hash=proposal.payload_hash(),
            issued_at=clock.now(),
            expires_at=clock.now() + timedelta(minutes=4),
            idempotency_key="task-17-reconcile",
            actor=ACTOR,
            correlation_id=CORRELATION,
            outcome="compare owned calendar projection",
        ),
    )


@pytest.mark.parametrize("mode", [AuthorityMode.MANUAL, AuthorityMode.COPILOT])
def test_manual_and_copilot_never_execute_automatically(
    clock: FakeClock,
    mode: AuthorityMode,
) -> None:
    policy = AuthorityProfilePolicy(clock)

    decision = policy.authorize(mode, AuthorityAction.CALENDAR_RULE_APPLY)

    assert decision.allowed is False
    assert decision.requires_confirmation is (mode == AuthorityMode.MANUAL)
    assert decision.may_propose is True


@pytest.mark.parametrize(
    "action",
    [
        AuthorityAction.SPEND,
        AuthorityAction.DELETE,
        AuthorityAction.SEND,
        AuthorityAction.BROWSER,
        AuthorityAction.COMPUTER,
        AuthorityAction.DOCUMENT,
    ],
)
def test_autopilot_denies_consequential_and_arbitrary_worker_actions(
    clock: FakeClock,
    action: AuthorityAction,
) -> None:
    policy = AuthorityProfilePolicy(clock)

    decision = policy.authorize(AuthorityMode.AUTOPILOT, action)

    assert decision.allowed is False
    assert decision.reason == "autopilot_action_denied"


def test_autopilot_accepts_only_approved_reversible_lifeos_calendar_rule(
    clock: FakeClock,
) -> None:
    issuer = LeaseIssuer(clock, SigningKeyRing(b"task-17-lease-key"), timedelta(minutes=5))
    authorization = calendar_authorization(clock, issuer)
    policy = AuthorityProfilePolicy(clock, lease_issuer=issuer)

    decision = policy.authorize(
        AuthorityMode.AUTOPILOT,
        AuthorityAction.CALENDAR_RULE_APPLY,
        authorization,
    )

    assert decision.allowed is True
    assert decision.reason == "approved_reversible_calendar_rule"
    assert decision.requires_confirmation is False


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("unapproved_rule", "approved_learning_rule_required"),
        ("foreign_rule", "lifeos_calendar_rule_required"),
        ("wrong_action", "calendar_apply_proposal_required"),
        ("missing_rollback", "rollback_required"),
        ("missing_reconciliation", "reconciliation_required"),
        ("forged_lease", "lease signature is invalid (forged or key rotated)"),
    ],
)
def test_autopilot_calendar_gate_fails_closed(
    clock: FakeClock,
    change: str,
    reason: str,
) -> None:
    issuer = LeaseIssuer(clock, SigningKeyRing(b"task-17-lease-key"), timedelta(minutes=5))
    authorization = calendar_authorization(clock, issuer)
    match change:
        case "unapproved_rule":
            authorization = authorization.model_copy(
                update={
                    "rule": authorization.rule.model_copy(
                        update={"status": ProposalStatus.PROPOSED}
                    )
                }
            )
        case "foreign_rule":
            authorization = authorization.model_copy(
                update={
                    "rule": authorization.rule.model_copy(
                        update={
                            "trigger": ObservationTrigger(kind="calendar_rule", source="channel")
                        }
                    )
                }
            )
        case "wrong_action":
            authorization = authorization.model_copy(
                update={
                    "proposal": authorization.proposal.model_copy(
                        update={"action_class": "message.send"}
                    )
                }
            )
        case "missing_rollback":
            authorization = authorization.model_copy(update={"rollback": None})
        case "missing_reconciliation":
            authorization = authorization.model_copy(update={"reconciliation": None})
        case "forged_lease":
            authorization = authorization.model_copy(
                update={"lease": authorization.lease.model_copy(update={"signature": "forged"})}
            )
        case unreachable:
            raise AssertionError(unreachable)

    decision = AuthorityProfilePolicy(clock, lease_issuer=issuer).authorize(
        AuthorityMode.AUTOPILOT,
        AuthorityAction.CALENDAR_RULE_APPLY,
        authorization,
    )

    assert decision.allowed is False
    assert decision.reason == reason


def test_autopilot_rejects_expired_and_replayed_calendar_authorization(
    clock: FakeClock,
) -> None:
    issuer = LeaseIssuer(clock, SigningKeyRing(b"task-17-lease-key"), timedelta(minutes=5))
    authorization = calendar_authorization(clock, issuer)
    policy = AuthorityProfilePolicy(clock, lease_issuer=issuer)

    first = policy.authorize(
        AuthorityMode.AUTOPILOT,
        AuthorityAction.CALENDAR_RULE_APPLY,
        authorization,
    )
    replay = policy.authorize(
        AuthorityMode.AUTOPILOT,
        AuthorityAction.CALENDAR_RULE_APPLY,
        authorization,
    )

    assert first.allowed is True
    assert replay.allowed is False
    assert replay.reason == "lease replay"

    expiring = calendar_authorization(clock, issuer)
    clock.advance(timedelta(minutes=5))
    expired = policy.authorize(
        AuthorityMode.AUTOPILOT,
        AuthorityAction.CALENDAR_RULE_APPLY,
        expiring,
    )
    assert expired.allowed is False
    assert expired.reason in {"lease has expired", "rollback_expired", "reconciliation_expired"}
