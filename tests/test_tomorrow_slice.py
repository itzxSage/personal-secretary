"""End-to-end tests for the natural-language tomorrow planning slice."""

from collections.abc import Iterator
from pathlib import Path
from typing import Final

import pytest

from secretary_service.keys import DeterministicTestKeyProvider
from secretary_service.slice.models import TomorrowFixture
from secretary_service.slice.runtime import SliceRuntime, build_fixture_runtime
from secretary_service.storage import EncryptedStateStore

ROOT: Final = Path(__file__).resolve().parents[1]
FIXTURE_PATH: Final = ROOT / "fixtures" / "tomorrow-plan.json"
SANDBOX_PATH: Final = ROOT / "fixtures" / "google" / "sandbox.json"


@pytest.fixture
def tomorrow_fixture() -> TomorrowFixture:
    return TomorrowFixture.model_validate_json(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def slice_runtime(
    tmp_path: Path,
    tomorrow_fixture: TomorrowFixture,
) -> Iterator[SliceRuntime]:
    keys = DeterministicTestKeyProvider.from_seed(b"task-8-tomorrow-slice")
    with EncryptedStateStore.open(tmp_path / "slice.sqlite", keys, tomorrow_fixture.clock) as store:
        yield build_fixture_runtime(tomorrow_fixture, SANDBOX_PATH, store)


def test_fixture_text_produces_exact_audited_preview(
    slice_runtime: SliceRuntime,
    tomorrow_fixture: TomorrowFixture,
) -> None:
    # Given: a typed Conversation API capture containing the fixture text.
    capture = tomorrow_fixture.initial_capture

    # When: the vertical slice interprets and plans tomorrow.
    preview = slice_runtime.service.preview(capture)

    # Then: the complete reasoning, minute plan, Calendar projection, and diff are golden.
    assert preview == tomorrow_fixture.golden.preview
    assert [entry.action_class for entry in slice_runtime.store.audit_entries()] == [
        "source_item.created",
        "proposal.created",
    ]


def test_device_approval_creates_only_owned_calendar_blocks(
    slice_runtime: SliceRuntime,
    tomorrow_fixture: TomorrowFixture,
) -> None:
    # Given: an exact calendar preview awaiting device-signed approval.
    preview = slice_runtime.service.preview(tomorrow_fixture.initial_capture)

    # When: the payload-bound approval is applied.
    applied = slice_runtime.service.approve(
        preview.authorization.proposal_id,
        tomorrow_fixture.approval,
    )

    # Then: every projected block is owned and the protected primary calendar is unchanged.
    assert applied == tomorrow_fixture.golden.apply
    assert len(slice_runtime.sandbox.proposed_events) == len(preview.plan.calendar_projection)
    assert all(event.ownership is not None for event in slice_runtime.sandbox.proposed_events)
    assert slice_runtime.sandbox.primary_events[0].event_id == "primary-protected"


def test_changed_shift_replan_preserves_protected_constraints(
    slice_runtime: SliceRuntime,
    tomorrow_fixture: TomorrowFixture,
) -> None:
    # Given: tomorrow's first plan has been approved and applied.
    preview = slice_runtime.service.preview(tomorrow_fixture.initial_capture)
    _ = slice_runtime.service.approve(
        preview.authorization.proposal_id,
        tomorrow_fixture.approval,
    )

    # When: a later transcript reports the changed work shift.
    replan = slice_runtime.service.replan(tomorrow_fixture.replan)

    # Then: the exact moved-block diff is visible and protected Farmers time is retained.
    assert replan == tomorrow_fixture.golden.replan
    assert replan.preserved_protected_activity_ids == ("farmers",)
    protected = next(
        block for block in replan.preview.plan.internal_plan if block.activity_id == "farmers"
    )
    assert protected.starts_at.isoformat() == "2026-09-07T17:15:00-04:00"
    assert protected.ends_at.isoformat() == "2026-09-07T17:45:00-04:00"


def test_payload_bound_rollback_removes_sandbox_events(
    slice_runtime: SliceRuntime,
    tomorrow_fixture: TomorrowFixture,
) -> None:
    # Given: an approved plan owns events in the sandbox calendar.
    preview = slice_runtime.service.preview(tomorrow_fixture.initial_capture)
    _ = slice_runtime.service.approve(
        preview.authorization.proposal_id,
        tomorrow_fixture.approval,
    )

    # When: its matching rollback fact is exercised.
    result = slice_runtime.service.rollback(
        preview.authorization.proposal_id,
        tomorrow_fixture.rollback,
    )

    # Then: only the owned events are deleted and the proposal is audited as reverted.
    assert result == tomorrow_fixture.golden.cleanup
    assert slice_runtime.sandbox.proposed_events == ()
    assert slice_runtime.store.audit_entries()[-1].action_class == "proposal.transitioned"


def test_cleanup_after_replan_removes_all_sandbox_events(
    slice_runtime: SliceRuntime,
    tomorrow_fixture: TomorrowFixture,
) -> None:
    # Given: an applied plan and a non-mutating changed-shift replan preview.
    preview = slice_runtime.service.preview(tomorrow_fixture.initial_capture)
    _ = slice_runtime.service.approve(
        preview.authorization.proposal_id,
        tomorrow_fixture.approval,
    )
    _ = slice_runtime.service.replan(tomorrow_fixture.replan)

    # When: the E2E cleanup path runs with the original payload binding.
    cleanup = slice_runtime.service.cleanup(
        preview.authorization.proposal_id,
        tomorrow_fixture.rollback,
    )

    # Then: cleanup is exact and no sandbox event survives.
    assert cleanup == tomorrow_fixture.golden.cleanup
    assert slice_runtime.sandbox.proposed_events == ()
