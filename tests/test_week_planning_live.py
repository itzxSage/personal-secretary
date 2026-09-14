"""Exercise live composition with only provider I/O replaced for hermetic tests."""

from pathlib import Path
from typing import final

import pytest
from pydantic import SecretStr

from secretary_service import week_planning_live
from secretary_service.enrollment import DeviceRegistry
from secretary_service.google_calendar_contract import (
    DEFAULT_REFRESH_REFERENCE,
    GOOGLE_CALENDAR_APP_SCOPE,
    CalendarOAuthGrant,
)
from secretary_service.google_calendar_live import CalendarIdStore, GoogleHTTPClient
from secretary_service.google_calendar_sandbox import GoogleCalendarSandbox
from secretary_service.keys import DeterministicTestKeyProvider, KeyProvider
from secretary_service.life_knowledge import RoutineFlexibility
from secretary_service.storage import EncryptedStateStore
from secretary_service.week_planning import WeekPlanProposalError
from tests.goals_memory_helpers import context
from tests.helpers import FakeClock
from tests.test_google_calendar import AUTH_FIXTURE, PRIMARY_EVENT_ID
from tests.test_week_planning import DEVICE_UUID, approval, enroll, persisted, routine, seed


@final
class TestTokenSource:
    __test__ = False

    def __init__(self, keys: KeyProvider) -> None:
        self.calls = 0

    def access_token(self, grant: CalendarOAuthGrant) -> SecretStr:
        assert grant.secret_reference == DEFAULT_REFRESH_REFERENCE
        assert grant.scopes == (GOOGLE_CALENDAR_APP_SCOPE,)
        self.calls += 1
        return SecretStr(AUTH_FIXTURE)


def test_live_composition_preserves_approval_and_restart_replay_protection(
    tmp_path: Path,
    clock: FakeClock,
    keys: DeterministicTestKeyProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = GoogleCalendarSandbox(valid_token=AUTH_FIXTURE, primary_event_id=PRIMARY_EVENT_ID)

    def transport(_calendar_ids: CalendarIdStore, _http: GoogleHTTPClient) -> GoogleCalendarSandbox:
        return sandbox

    monkeypatch.setattr(week_planning_live, "GoogleCalendarRESTTransport", transport)
    monkeypatch.setattr(week_planning_live, "GoogleRefreshTokenSource", TestTokenSource)
    factory = week_planning_live.live_week_planning_factory(clock, keys)
    assert sandbox.mutation_count == 0
    with EncryptedStateStore.open(tmp_path / "live-wiring.sqlite", keys, clock) as store:
        enroll(DeviceRegistry(clock, store.devices))
        seed(store, clock, routine(clock, 1, "Deep work", RoutineFlexibility.PREFERRED))
        service = factory(store)
        proposed = service.preview("user", clock.now(), "UTC")
        assert sandbox.mutation_count == 0
        signed = approval(clock, persisted(store, proposed.proposal_id))
        result = service.approve_and_apply(
            proposed.proposal_id, signed, context(clock, "apply"), DEVICE_UUID
        )
        assert result.state.value == "applied"
        assert result.applied_operations > 0
        writes = sandbox.mutation_count
        restarted = week_planning_live.live_week_planning_factory(clock, keys)(store)
        with pytest.raises(WeekPlanProposalError):
            _ = restarted.approve_and_apply(
                proposed.proposal_id, signed, context(clock, "replay"), DEVICE_UUID
            )
        assert sandbox.mutation_count == writes
