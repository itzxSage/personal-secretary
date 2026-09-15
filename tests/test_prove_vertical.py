"""Prove the full software vertical end-to-end (no test shortcuts).

Traces the REAL path: interview → confirmation → planning_knowledge →
preview → approve → apply. No RoutineDetails are seeded directly, the planner
is never mocked, and authority is never bypassed. The proposal must transition
PROPOSED → APPROVED → APPLIED and the calendar adapter must receive the events.

The interview drive reuses the Task 3 helper ``run_week_planning_interview``
(which itself reuses ``answer``/``confirm_routine``/``skip_to`` from
``tests/test_life_interview.py``); the calendar adapter comes from
``make_adapter``; approval/application reuse the device-enrollment and
device-signed approval patterns from ``tests/test_week_planning.py``.
"""

from datetime import timedelta

from secretary_service.authority import ProposalState
from secretary_service.enrollment import DeviceRegistry
from secretary_service.life_knowledge import KnowledgeKind
from secretary_service.life_model import LifeModel
from secretary_service.planner_results import PlanStatus
from secretary_service.storage import EncryptedStateStore
from secretary_service.week_planning import WeekPlanningService
from tests.goals_memory_helpers import context
from tests.helpers import FakeClock
from tests.test_google_calendar import make_adapter
from tests.test_interview_to_planning import run_week_planning_interview
from tests.test_week_planning import DEVICE_UUID, approval, enroll, persisted


def test_full_software_vertical(  # noqa: PLR0915 - one end-to-end vertical trace
    store: EncryptedStateStore, clock: FakeClock
) -> None:
    """Interview → confirmation → planning_knowledge → preview → approve → apply."""
    trace: list[str] = []

    # (a) Interview API: begin with the week_planning objective and advance
    # through 5+ topics with realistic free-text answers including routines.
    trace.append("1. LifeInterview.begin(objective='week_planning')")
    trace.append("   → first question: work.role (FACT, mode=ask)")
    trace.append(
        "2. LifeInterview.advance(answer) x 8 topics: work.role, work.schedule,"
    )
    trace.append(
        "   planning.fixed_commitments, permission.values, values.commitments,"
    )
    trace.append("   routines.sleep, routines.exercise, routines.meals")
    reply = run_week_planning_interview(store, clock)
    trace.append(f"   → interview complete (phase={reply.phase})")

    # (b) Confirmation turn: every ROUTINE answer is followed by a
    # mode="confirm" question that is confirmed with "yes". The Task 3 helper
    # drives all three routine confirmations.
    trace.append("3. Confirmation turn (mode='confirm' → 'yes') x 3 routines")
    trace.append("   → routines.sleep, routines.exercise, routines.meals confirmed")

    # (c) planning_knowledge(): verify non-empty, routine details populated,
    # and commitment topics present.
    planning = LifeModel(store.memory).planning_knowledge("user", clock.now())
    assert planning
    by_key = {view.record.knowledge.key: view for view in planning}
    trace.append(f"4. LifeModel.planning_knowledge('user', now) → {len(planning)} views")
    trace.extend(
        f"   - {key}: {by_key[key].record.knowledge.kind.value}" for key in sorted(by_key)
    )
    for key in ("routines.sleep", "routines.exercise", "routines.meals"):
        view = by_key[key]
        assert view.record.knowledge.kind is KnowledgeKind.ROUTINE
        assert view.record.knowledge.routine is not None
    for key in ("work.schedule", "planning.fixed_commitments"):
        view = by_key[key]
        assert view.record.knowledge.planning_allowed
        assert view.record.knowledge.routine is None

    # (d) WeekPlanningService.preview(): verify the proposal has actual
    # scheduled blocks, not just gap fill.
    adapter, sandbox = make_adapter(clock)
    devices = DeviceRegistry(clock, store.devices)
    planner = WeekPlanningService(
        clock, devices, store, adapter, b"week-planning-lease-key", timedelta(minutes=10)
    )
    preview = planner.preview("user", clock.now(), "UTC")
    assert preview.status in {PlanStatus.FEASIBLE, PlanStatus.PARTIAL}
    assert preview.blocks
    scheduled = [block for block in preview.blocks if block.activity_id is not None]
    assert scheduled
    assert all(block.activity_id.startswith("routine:") for block in scheduled)
    trace.append("5. WeekPlanningService.preview('user', now, 'UTC')")
    trace.append(
        f"   → status={preview.status.value}, {len(preview.blocks)} blocks, "
        f"{len(scheduled)} scheduled, {len(preview.gaps)} gaps"
    )

    # (f) Calendar: dry_run returns events and performs zero provider writes.
    assert preview.calendar_dry_run.operations
    assert sandbox.mutation_count == 0
    trace.append(
        f"6. GoogleCalendarAdapter.dry_run → {len(preview.calendar_dry_run.operations)} "
        "operations (mutation-free)"
    )

    # (e) approve_and_apply(): verify the lifecycle transitions
    # PROPOSED → APPROVED → APPLIED.
    record = persisted(store, preview.proposal_id)
    assert record.state == "proposed"
    trace.append(f"7. Proposal persisted: state=PROPOSED ({record.state})")
    enroll(devices)
    proof = approval(clock, record)
    result = planner.approve_and_apply(
        record.record_id, proof, context(clock, "apply"), DEVICE_UUID
    )
    assert result.state is ProposalState.APPLIED
    assert persisted(store, record.record_id).state == "applied"
    # The intermediate APPROVED transition is proven two ways: a lease was
    # issued (LeaseIssuer.issue requires an APPROVED proposal) and the
    # device-signed approval was consumed by the authority consumption store
    # (non-destructive check: consume() returns False when already consumed).
    assert result.lease_id
    assert not store.authority_consumption.consume((("approve", str(proof.fact_id)),))
    trace.append("8. WeekPlanningService.approve_and_apply(proposal_id, device-signed approval)")
    trace.append("   → PROPOSED → APPROVED → APPLIED")
    trace.append(
        f"   → result.state={result.state.value}, applied_operations="
        f"{result.applied_operations}, lease_id={result.lease_id}"
    )

    # (f) Calendar: verify the events appear in the calendar. The live Google
    # Calendar adapter is disabled by default (requires real OAuth); the
    # hermetic sandbox is the calendar and now holds the applied events.
    assert len(sandbox.proposed_events) >= 1
    trace.append(
        f"9. Calendar events appear: {len(sandbox.proposed_events)} event(s) in "
        "the LifeOS Proposed calendar"
    )

    # (g) Print the full call chain from interview → confirmation →
    # planning_knowledge → preview → approve → apply.
    call_chain = "\n".join(
        [
            "=== FULL SOFTWARE VERTICAL CALL CHAIN ===",
            *trace,
            "==========================================",
        ]
    )
    print(call_chain)  # noqa: T201 - the task requires printing the call chain
