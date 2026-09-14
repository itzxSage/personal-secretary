"""Governed Life Model to week-plan preview and calendar execution tests.

# noqa: SIZE_OK - comprehensive scenarios must remain in this user-scoped test file.
"""

from datetime import time, timedelta
from pathlib import Path
from typing import Final, Literal, NoReturn
from uuid import UUID

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import TypeAdapter, ValidationError

from secretary_service.authority import Approval, ApprovalProof, ProposalRecord, ProposalState
from secretary_service.enrollment import DeviceId, DeviceRegistry
from secretary_service.google_calendar import CalendarPlan
from secretary_service.google_calendar_contract import CalendarEventDraft, calendar_payload
from secretary_service.keys import DeterministicTestKeyProvider
from secretary_service.leases import LeaseIssuer, LeaseRequest
from secretary_service.life_knowledge import (
    KnowledgeKind,
    OpenLoopDetails,
    ProjectDetails,
    RoutineDetails,
    RoutineFlexibility,
)
from secretary_service.memory import MemoryRecord
from secretary_service.models import ActorId, CorrelationId, Proposal, RecordId, RecordKind
from secretary_service.planner import propose_week
from secretary_service.planner_models import WeekPlanRequest
from secretary_service.planner_results import PlanBlockKind, PlanStatus, ProposedDay
from secretary_service.storage import EncryptedStateStore
from secretary_service.week_planning import (
    WeekPlanApproval,
    WeekPlanningPolicyError,
    WeekPlanningProviderError,
    WeekPlanningService,
    WeekPlanProposalError,
)
from tests.goals_memory_helpers import context, record_id
from tests.helpers import FakeClock
from tests.test_google_calendar import make_adapter
from tests.test_life_knowledge import assertion

PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
DEVICE_UUID = UUID("70000000-0000-0000-0000-000000000001")
EVENTS = TypeAdapter(tuple[CalendarEventDraft, ...])
DEFAULT_START: Final = time(10)


def routine(  # noqa: PLR0913 - compact routine fixture builder
    clock: FakeClock,
    number: int,
    title: str,
    flexibility: RoutineFlexibility,
    *,
    day: int = 4,
    start: time | None = DEFAULT_START,
    dependency_ids: tuple[RecordId, ...] = (),
    preparation: int = 0,
    travel: int = 0,
    transition: int = 0,
) -> MemoryRecord:
    base = assertion(clock, number)
    assert base.knowledge is not None
    details = base.knowledge.model_copy(
        update={
            "key": f"routine.{number}",
            "kind": KnowledgeKind.ROUTINE,
            "routine": RoutineDetails(
                days=frozenset({day}),
                start_time=start,
                duration_minutes=60,
                timezone="UTC",
                flexibility=flexibility,
                priority=8,
                can_move=flexibility is not RoutineFlexibility.FIXED,
                dependency_ids=dependency_ids,
                preparation_minutes=preparation,
                travel_minutes=travel,
                transition_minutes=transition,
            ),
        }
    )
    return base.model_copy(update={"content": title, "knowledge": details})


def structured(
    clock: FakeClock,
    number: int,
    kind: Literal[KnowledgeKind.PROJECT, KnowledgeKind.OPEN_LOOP, KnowledgeKind.FACT],
) -> MemoryRecord:
    base = assertion(clock, number)
    assert base.knowledge is not None
    details = {
        KnowledgeKind.PROJECT: base.knowledge.model_copy(
            update={
                "key": f"structured.{number}",
                "kind": KnowledgeKind.PROJECT,
                "project": ProjectDetails(desired_outcome="Ship the release"),
            }
        ),
        KnowledgeKind.OPEN_LOOP: base.knowledge.model_copy(
            update={
                "key": f"structured.{number}",
                "kind": KnowledgeKind.OPEN_LOOP,
                "open_loop": OpenLoopDetails(next_action="Clarify it"),
            }
        ),
        KnowledgeKind.FACT: base.knowledge.model_copy(
            update={"key": f"structured.{number}", "kind": KnowledgeKind.FACT}
        ),
    }[kind]
    return base.model_copy(update={"content": kind.value, "knowledge": details})


def service(
    store: EncryptedStateStore, clock: FakeClock
) -> tuple[WeekPlanningService, DeviceRegistry]:
    adapter, _ = make_adapter(clock)
    devices = DeviceRegistry(clock, store.devices)
    return WeekPlanningService(
        clock, devices, store, adapter, b"week-planning-lease-key", timedelta(minutes=10)
    ), devices


def seed(store: EncryptedStateStore, clock: FakeClock, *records: MemoryRecord) -> None:
    for item in records:
        store.memory.remember(item, context(clock, "seed"))


def persisted(store: EncryptedStateStore, proposal_id: RecordId) -> Proposal:
    found = store.read(RecordKind.PROPOSAL, proposal_id)
    assert isinstance(found, Proposal)
    return found


def approval(clock: FakeClock, proposal: Proposal, *, expires_in: int = 5) -> Approval:
    unsigned = Approval(
        fact_id=record_id(900),
        proposal_id=proposal.record_id,
        payload_hash=ProposalRecord(
            proposal_id=proposal.record_id,
            action_class=proposal.proposal_type,
            payload=proposal.payload,
            state=ProposalState(proposal.state),
            created_at=proposal.created_at,
        ).payload_hash(),
        issued_at=clock.now(),
        expires_at=clock.now() + timedelta(minutes=expires_in),
        idempotency_key="approve-week-1",
        actor=ActorId("user"),
        correlation_id=CorrelationId("week-planning"),
        proof=ApprovalProof.DEVICE_SIGNED,
        device_id=DeviceId(str(DEVICE_UUID)),
    )
    return unsigned.model_copy(
        update={"signature": PRIVATE_KEY.sign(unsigned.signing_bytes("calendar.apply")).hex()}
    )


def enroll(devices: DeviceRegistry) -> None:
    _ = devices.enroll(
        DeviceId(str(DEVICE_UUID)),
        "iphone-fingerprint",
        ActorId("user"),
        approval_public_key=PRIVATE_KEY.public_key().public_bytes_raw().hex(),
    )


def test_preview_is_deterministic_feasible_and_covers_10080_minutes(
    store: EncryptedStateStore, clock: FakeClock
) -> None:
    # Given
    seed(store, clock, routine(clock, 1, "Deep work", RoutineFlexibility.PREFERRED))
    planner, _ = service(store, clock)
    # When
    first = planner.preview("user", clock.now(), "UTC")
    second = planner.preview("user", clock.now(), "UTC")
    # Then
    assert first.status is PlanStatus.FEASIBLE
    assert sum(block.duration_minutes for block in first.blocks) == 10_080
    assert first.blocks == second.blocks
    assert first.calendar_blocks == second.calendar_blocks
    assert first.explanations == second.explanations
    assert first.unscheduled == second.unscheduled == ()
    first_events = EVENTS.validate_json(persisted(store, first.proposal_id).payload)
    second_events = EVENTS.validate_json(persisted(store, second.proposal_id).payload)
    assert tuple(event.model_dump(exclude={"event_key"}) for event in first_events) == tuple(
        event.model_dump(exclude={"event_key"}) for event in second_events
    )


def test_fixed_routine_uses_exact_logistics_interval_and_is_not_an_event(
    store: EncryptedStateStore, clock: FakeClock
) -> None:
    # Given
    seed(
        store,
        clock,
        routine(
            clock,
            1,
            "Protected appointment",
            RoutineFlexibility.FIXED,
            day=3,
            start=time(8),
            preparation=10,
            travel=10,
            transition=5,
        ),
    )
    planner, _ = service(store, clock)
    # When
    result = planner.preview("user", clock.now(), "UTC")
    # Then
    fixed = next(block for block in result.blocks if block.kind is PlanBlockKind.FIXED)
    assert (fixed.starts_at.hour, fixed.starts_at.minute) == (7, 40)
    assert (fixed.ends_at.hour, fixed.ends_at.minute) == (9, 5)
    events = EVENTS.validate_json(persisted(store, result.proposal_id).payload)
    assert events == ()
    assert result.calendar_dry_run.operations == ()
    assert all(event.summary != "Protected appointment" for event in events)


def test_preferred_and_flexible_routines_become_calendar_events(
    store: EncryptedStateStore, clock: FakeClock
) -> None:
    # Given
    seed(store, clock, routine(clock, 1, "Deep work", RoutineFlexibility.PREFERRED))
    planner, _ = service(store, clock)
    # When
    result = planner.preview("user", clock.now(), "UTC")
    # Then
    events = EVENTS.validate_json(persisted(store, result.proposal_id).payload)
    assert [event.summary for event in events] == ["Deep work"]
    assert result.calendar_blocks[0].source_activity_ids
    assert result.explanations[0].score.goal == 800


def test_under_specified_project_fact_and_dependency_become_gaps_without_activities(
    store: EncryptedStateStore, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    unknown = record_id(99)
    seed(
        store,
        clock,
        structured(clock, 1, KnowledgeKind.PROJECT),
        structured(clock, 2, KnowledgeKind.FACT),
        routine(clock, 3, "Blocked", RoutineFlexibility.FLEXIBLE, dependency_ids=(unknown,)),
        structured(clock, 4, KnowledgeKind.OPEN_LOOP),
    )
    captured: list[WeekPlanRequest] = []

    def recording_propose(request: WeekPlanRequest) -> ProposedDay:
        captured.append(request)
        return propose_week(request)

    monkeypatch.setattr("secretary_service.week_planning.propose_week", recording_propose)
    planner, _ = service(store, clock)
    # When
    result = planner.preview("user", clock.now(), "UTC")
    # Then
    assert captured[0].activities == ()
    assert {gap.memory_id for gap in result.gaps} == {record_id(1), record_id(2), record_id(3)}
    assert record_id(4) not in {gap.memory_id for gap in result.gaps}


def test_preview_performs_zero_provider_writes(
    store: EncryptedStateStore, clock: FakeClock
) -> None:
    # Given
    seed(store, clock, routine(clock, 1, "Deep work", RoutineFlexibility.PREFERRED))
    adapter, sandbox = make_adapter(clock)
    planner = WeekPlanningService(
        clock, DeviceRegistry(clock), store, adapter, b"lease-key", timedelta(minutes=10)
    )
    # When
    result = planner.preview("user", clock.now(), "UTC")
    # Then
    assert sandbox.mutation_count == 0
    assert len(result.calendar_dry_run.operations) == 1


def test_persisted_payload_is_canonical_and_validates_calendar_plan(
    store: EncryptedStateStore, clock: FakeClock
) -> None:
    # Given
    seed(store, clock, routine(clock, 1, "Deep work", RoutineFlexibility.PREFERRED))
    planner, _ = service(store, clock)
    # When
    result = planner.preview("user", clock.now(), "UTC")
    # Then
    record = persisted(store, result.proposal_id)
    events = EVENTS.validate_json(record.payload)
    authorization = ProposalRecord(
        proposal_id=record.record_id,
        action_class=record.proposal_type,
        payload=record.payload,
        state=ProposalState.PROPOSED,
        created_at=record.created_at,
    )
    assert record.payload == calendar_payload(events)
    assert CalendarPlan(authorization=authorization, events=events).events == events


@pytest.mark.parametrize("failure", ["signature", "payload", "expired", "device", "stale"])
def test_invalid_approval_never_issues_lease_or_calls_provider(
    store: EncryptedStateStore,
    clock: FakeClock,
    failure: Literal["signature", "payload", "expired", "device", "stale"],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    seed(store, clock, routine(clock, 1, "Deep work", RoutineFlexibility.PREFERRED))
    adapter, sandbox = make_adapter(clock)
    devices = DeviceRegistry(clock)
    enroll(devices)
    planner = WeekPlanningService(
        clock, devices, store, adapter, b"lease-key", timedelta(minutes=10)
    )
    preview = planner.preview("user", clock.now(), "UTC")
    record = persisted(store, preview.proposal_id)
    proof = approval(clock, record)
    expected_device = DEVICE_UUID
    if failure == "signature":
        proof = proof.model_copy(update={"signature": "00" * 64})
    elif failure == "payload":
        proof = proof.model_copy(update={"payload_hash": "0" * 64})
    elif failure == "expired":
        clock.advance(timedelta(minutes=6))
    elif failure == "device":
        expected_device = UUID("70000000-0000-0000-0000-000000000002")
    else:
        store.transition(
            RecordKind.PROPOSAL, record.record_id, "rejected", context(clock, "reject")
        )

    def unexpected_issue(
        _lease_issuer: LeaseIssuer, _proposal: ProposalRecord, _request: LeaseRequest
    ) -> NoReturn:
        message = "invalid approval reached lease issuance"
        raise AssertionError(message)

    monkeypatch.setattr(LeaseIssuer, "issue", unexpected_issue)
    # When / Then
    error = WeekPlanProposalError if failure == "stale" else WeekPlanningPolicyError
    with pytest.raises(error):
        _ = planner.approve_and_apply(
            record.record_id, proof, context(clock, "apply"), expected_device
        )
    assert sandbox.mutation_count == 0


def test_unknown_proposal_is_rejected_before_external_work(
    store: EncryptedStateStore, clock: FakeClock
) -> None:
    # Given
    planner, _ = service(store, clock)
    missing = Proposal(
        record_id=record_id(88),
        created_at=clock.now(),
        state="proposed",
        proposal_type="calendar.apply",
        payload="[]",
        policy_decision="approval-required",
        provider_version="google.calendar.v3",
        reversible=True,
        approval_state="proposed",
    )
    # When / Then
    with pytest.raises(WeekPlanProposalError, match="unknown_proposal"):
        _ = planner.approve_and_apply(
            missing.record_id,
            approval(clock, missing),
            context(clock, "apply"),
            DEVICE_UUID,
        )


def test_valid_device_approval_applies_once_under_verified_lease(
    store: EncryptedStateStore, clock: FakeClock
) -> None:
    # Given
    seed(store, clock, routine(clock, 1, "Deep work", RoutineFlexibility.PREFERRED))
    adapter, sandbox = make_adapter(clock)
    devices = DeviceRegistry(clock)
    enroll(devices)
    planner = WeekPlanningService(
        clock, devices, store, adapter, b"lease-key", timedelta(minutes=10)
    )
    preview = planner.preview("user", clock.now(), "UTC")
    record = persisted(store, preview.proposal_id)
    # When
    result = planner.approve_and_apply(
        record.record_id, approval(clock, record), context(clock, "apply"), DEVICE_UUID
    )
    # Then
    assert result.state is ProposalState.APPLIED
    assert result.applied_operations == 1
    assert result.lease_id
    assert persisted(store, record.record_id).state == "applied"
    assert len(sandbox.proposed_events) == 1
    assert len(sandbox.primary_events) == 1


def test_retry_of_same_approval_fails_without_duplicate_event(
    store: EncryptedStateStore, clock: FakeClock
) -> None:
    # Given
    seed(store, clock, routine(clock, 1, "Deep work", RoutineFlexibility.PREFERRED))
    adapter, sandbox = make_adapter(clock)
    devices = DeviceRegistry(clock)
    enroll(devices)
    planner = WeekPlanningService(
        clock, devices, store, adapter, b"lease-key", timedelta(minutes=10)
    )
    preview = planner.preview("user", clock.now(), "UTC")
    record = persisted(store, preview.proposal_id)
    proof = approval(clock, record)
    _ = planner.approve_and_apply(record.record_id, proof, context(clock, "apply"), DEVICE_UUID)
    mutations = sandbox.mutation_count
    # When / Then
    with pytest.raises(WeekPlanProposalError, match="stale_proposal"):
        _ = planner.approve_and_apply(record.record_id, proof, context(clock, "retry"), DEVICE_UUID)
    assert sandbox.mutation_count == mutations
    assert len(sandbox.proposed_events) == 1


def test_provider_failure_preserves_approved_state_without_claiming_success(
    store: EncryptedStateStore, clock: FakeClock
) -> None:
    # Given
    seed(store, clock, routine(clock, 1, "Deep work", RoutineFlexibility.PREFERRED))
    adapter, sandbox = make_adapter(clock)
    devices = DeviceRegistry(clock)
    enroll(devices)
    planner = WeekPlanningService(
        clock, devices, store, adapter, b"lease-key", timedelta(minutes=10)
    )
    preview = planner.preview("user", clock.now(), "UTC")
    record = persisted(store, preview.proposal_id)
    sandbox.revoke_token()
    # When / Then
    with pytest.raises(WeekPlanningProviderError, match="calendar_authorization"):
        _ = planner.approve_and_apply(
            record.record_id, approval(clock, record), context(clock, "apply"), DEVICE_UUID
        )
    assert persisted(store, record.record_id).state == "approved"
    assert sandbox.mutation_count == 0


def test_persisted_proposal_and_device_survive_restart_for_approval(
    tmp_path: Path, keys: DeterministicTestKeyProvider, clock: FakeClock
) -> None:
    # Given
    path = tmp_path / "week-planning.sqlite"
    adapter, sandbox = make_adapter(clock)
    with EncryptedStateStore.open(path, keys, clock) as first:
        seed(first, clock, routine(clock, 1, "Deep work", RoutineFlexibility.PREFERRED))
        devices = DeviceRegistry(clock, first.devices)
        enroll(devices)
        preview = WeekPlanningService(
            clock, devices, first, adapter, b"lease-key", timedelta(minutes=10)
        ).preview("user", clock.now(), "UTC")
        proof = approval(clock, persisted(first, preview.proposal_id))
    # When
    with EncryptedStateStore.open(path, keys, clock) as reopened:
        planner = WeekPlanningService(
            clock,
            DeviceRegistry(clock, reopened.devices),
            reopened,
            adapter,
            b"lease-key",
            timedelta(minutes=10),
        )
        result = planner.approve_and_apply(
            preview.proposal_id, proof, context(clock, "apply"), DEVICE_UUID
        )
    # Then
    assert result.state is ProposalState.APPLIED
    assert len(sandbox.proposed_events) == 1


def test_boundary_models_are_frozen_and_forbid_extra_fields(clock: FakeClock) -> None:
    # Given
    proposal = Proposal(
        record_id=record_id(77),
        created_at=clock.now(),
        state="proposed",
        proposal_type="calendar.apply",
        payload="[]",
        policy_decision="approval-required",
        provider_version="google.calendar.v3",
        reversible=True,
        approval_state="proposed",
    )
    proof = WeekPlanApproval(approval=approval(clock, proposal))
    # When / Then
    with pytest.raises(ValidationError):
        _ = WeekPlanApproval.model_validate({"approval": proof.approval, "extra": True})
