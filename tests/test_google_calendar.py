"""Google Calendar proposal-first adapter tests."""

from datetime import timedelta
from importlib.util import find_spec
from uuid import UUID

import pytest
from pydantic import ValidationError

from secretary_service.authority import (
    ApprovalProof,
    AuthorityTier,
    ProposalRecord,
    ProposalState,
    Rollback,
    default_approval_matrix,
)
from secretary_service.google_calendar import (
    GOOGLE_CALENDAR_APP_SCOPE,
    CalendarAdapterConfig,
    CalendarAdapterDependencies,
    CalendarAuthorizationError,
    CalendarContractError,
    CalendarEventDraft,
    CalendarOAuthGrant,
    CalendarOperation,
    CalendarPlan,
    GoogleCalendarAdapter,
    KeychainOAuthTokenSource,
    calendar_payload,
)
from secretary_service.google_calendar_sandbox import GoogleCalendarSandbox
from secretary_service.models import ActorId, CorrelationId, RecordId
from tests.helpers import FakeClock

AUTH_FIXTURE = "sandbox-access-token"
KEYCHAIN_POINTER = "keychain://google-calendar/oauth"
PRIMARY_EVENT_ID = "primary-existing"


class FixtureKeyProvider:
    def database_key(self) -> bytes:
        return b"d" * 32

    def audit_key(self) -> bytes:
        return b"a" * 32

    def backup_wrapping_key(self) -> bytes:
        return b"b" * 32

    def connector_secret(self, reference: str) -> str:
        assert reference == KEYCHAIN_POINTER
        return AUTH_FIXTURE


def make_event(clock: FakeClock) -> CalendarEventDraft:
    return CalendarEventDraft(
        event_key="workout",
        summary="Workout",
        starts_at=clock.now() + timedelta(hours=18),
        ends_at=clock.now() + timedelta(hours=19),
    )


def make_plan(clock: FakeClock, state: ProposalState) -> CalendarPlan:
    events = (make_event(clock),)
    return CalendarPlan(
        authorization=ProposalRecord(
            proposal_id=RecordId(UUID("00000000-0000-0000-0000-000000000701")),
            action_class="calendar.apply",
            payload=calendar_payload(events),
            state=state,
            created_at=clock.now(),
        ),
        events=events,
    )


def make_adapter(
    clock: FakeClock,
) -> tuple[GoogleCalendarAdapter, GoogleCalendarSandbox]:
    sandbox = GoogleCalendarSandbox(valid_token=AUTH_FIXTURE, primary_event_id=PRIMARY_EVENT_ID)
    grant = CalendarOAuthGrant(
        secret_reference=KEYCHAIN_POINTER,
        scopes=(GOOGLE_CALENDAR_APP_SCOPE,),
    )
    adapter = GoogleCalendarAdapter(
        CalendarAdapterConfig(owner_id="lifeos-user", oauth_grant=grant),
        CalendarAdapterDependencies(
            transport=sandbox,
            token_source=KeychainOAuthTokenSource(FixtureKeyProvider()),
            clock=clock,
        ),
    )
    return adapter, sandbox


def test_baseline_calendar_apply_requires_device_approved_act_authority() -> None:
    matrix = default_approval_matrix()

    rule = matrix.rule_for("calendar.apply")

    assert rule is not None
    assert rule.required_tier == AuthorityTier.ACT
    assert rule.proof_required
    assert rule.allowed_proofs == (ApprovalProof.DEVICE_SIGNED,)


def test_google_calendar_adapter_contract_is_available() -> None:
    module = find_spec("secretary_service.google_calendar")

    assert module is not None


def test_oauth_grant_rejects_broader_scope_and_keeps_token_out_of_results(
    clock: FakeClock,
) -> None:
    with pytest.raises(ValidationError, match="least-scope"):
        _ = CalendarOAuthGrant(
            secret_reference=KEYCHAIN_POINTER,
            scopes=("https://www.googleapis.com/auth/calendar",),
        )
    adapter, _ = make_adapter(clock)

    preview = adapter.dry_run(make_plan(clock, ProposalState.PROPOSED))

    assert AUTH_FIXTURE not in repr(adapter)
    assert AUTH_FIXTURE not in preview.model_dump_json()


def test_malformed_event_is_rejected_before_calendar_access(clock: FakeClock) -> None:
    adapter, sandbox = make_adapter(clock)

    with pytest.raises(ValidationError):
        _ = CalendarEventDraft(
            event_key="bad key!",
            summary="Malformed",
            starts_at=clock.now() + timedelta(hours=2),
            ends_at=clock.now() + timedelta(hours=1),
        )

    assert sandbox.mutation_count == 0
    assert adapter.sync(None).events == ()


def test_dry_run_is_proposal_only_and_does_not_create_calendar(clock: FakeClock) -> None:
    adapter, sandbox = make_adapter(clock)

    preview = adapter.dry_run(make_plan(clock, ProposalState.PROPOSED))

    assert preview.operations[0].operation == CalendarOperation.INSERT
    assert sandbox.proposed_calendar_id is None
    assert sandbox.mutation_count == 0


def test_apply_requires_approved_plan_and_never_mutates_primary(clock: FakeClock) -> None:
    adapter, sandbox = make_adapter(clock)
    primary_before = sandbox.primary_events
    with pytest.raises(CalendarAuthorizationError, match="approved"):
        _ = adapter.apply(make_plan(clock, ProposalState.PROPOSED))

    result = adapter.apply(make_plan(clock, ProposalState.APPROVED))

    assert len(sandbox.proposed_events) == 1
    assert result.operations[0].operation == CalendarOperation.INSERT
    assert sandbox.primary_events == primary_before
    assert all(event.ownership is not None for event in sandbox.proposed_events)


def test_revoked_token_fails_before_any_mutation(clock: FakeClock) -> None:
    adapter, sandbox = make_adapter(clock)
    sandbox.revoke_token()

    with pytest.raises(CalendarAuthorizationError, match="revoked"):
        _ = adapter.apply(make_plan(clock, ProposalState.APPROVED))

    assert sandbox.proposed_calendar_id is None
    assert sandbox.mutation_count == 0


def test_transient_retry_and_crash_retry_do_not_duplicate(clock: FakeClock) -> None:
    adapter, sandbox = make_adapter(clock)
    plan = make_plan(clock, ProposalState.APPROVED)
    sandbox.fail_next_writes(2)

    first = adapter.apply(plan)
    sandbox.interrupt_next_write_after_commit()
    changed_plan = plan.model_copy(
        update={"events": (plan.events[0].model_copy(update={"summary": "Gym workout"}),)}
    )
    changed_plan = changed_plan.model_copy(
        update={
            "authorization": plan.authorization.model_copy(
                update={"payload": calendar_payload(changed_plan.events)}
            )
        }
    )
    with pytest.raises(ConnectionError, match="interrupted"):
        _ = adapter.apply(changed_plan)

    retried = adapter.apply(changed_plan)

    assert first.attempts == 3
    assert retried.operations[0].operation == CalendarOperation.NOOP
    assert len(sandbox.proposed_events) == 1


def test_external_edit_and_stale_sync_are_reconciled_then_rolled_back(
    clock: FakeClock,
) -> None:
    adapter, sandbox = make_adapter(clock)
    approved = make_plan(clock, ProposalState.APPROVED)
    applied = adapter.apply(approved)
    event_id = sandbox.proposed_events[0].event_id
    sandbox.external_edit(event_id, summary="Edited in Google")
    sandbox.expire_sync_tokens()

    reconciliation = adapter.reconcile(approved, applied.sync_state)

    assert reconciliation.operations[0].operation == CalendarOperation.EXTERNAL_EDIT
    assert sandbox.proposed_events[0].summary == "Edited in Google"
    applied_plan = approved.model_copy(
        update={"authorization": approved.authorization.model_copy(update={"state": "applied"})}
    )
    rollback = Rollback(
        fact_id=RecordId(UUID("00000000-0000-0000-0000-000000000702")),
        proposal_id=applied_plan.authorization.proposal_id,
        payload_hash=applied_plan.authorization.payload_hash(),
        issued_at=clock.now(),
        expires_at=clock.now() + timedelta(minutes=5),
        idempotency_key="rollback-calendar-1",
        actor=ActorId("user"),
        correlation_id=CorrelationId("corr-calendar"),
        reason="user requested undo",
    )
    rolled_back = adapter.rollback(applied_plan, rollback)

    assert rolled_back.operations[0].operation == CalendarOperation.DELETE
    assert sandbox.proposed_events == ()
    assert sandbox.primary_events[0].event_id == PRIMARY_EVENT_ID


def test_misleading_provider_output_is_rejected_and_retry_reconciles(
    clock: FakeClock,
) -> None:
    adapter, sandbox = make_adapter(clock)
    plan = make_plan(clock, ProposalState.APPROVED)
    sandbox.misreport_next_write()

    with pytest.raises(CalendarContractError, match="mismatched"):
        _ = adapter.apply(plan)

    retry = adapter.apply(plan)

    assert retry.operations[0].operation == CalendarOperation.NOOP
    assert len(sandbox.proposed_events) == 1
