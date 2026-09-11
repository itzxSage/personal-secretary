"""Hermetic runtime wiring for the tomorrow planning vertical slice."""

from dataclasses import dataclass
from pathlib import Path
from typing import final

from pydantic import SecretStr

from secretary_service.enrollment import DeviceId, DeviceRegistry
from secretary_service.fixture_authority import FIXTURE_PUBLIC_KEY
from secretary_service.google_calendar import GoogleCalendarAdapter
from secretary_service.google_calendar_contract import (
    GOOGLE_CALENDAR_APP_SCOPE,
    CalendarAdapterConfig,
    CalendarAdapterDependencies,
    CalendarOAuthGrant,
)
from secretary_service.google_calendar_errors import CalendarAuthorizationError
from secretary_service.google_calendar_sandbox import GoogleCalendarSandbox
from secretary_service.models import ActorId
from secretary_service.slice.interpreter import FixtureInterpretationAdapter
from secretary_service.slice.models import SandboxFixture, TomorrowFixture
from secretary_service.slice.service import TomorrowPlanningSlice
from secretary_service.storage import EncryptedStateStore


@dataclass(frozen=True, slots=True)
class SliceRuntime:
    """Bound slice service with its encrypted store and calendar sandbox."""

    service: TomorrowPlanningSlice
    store: EncryptedStateStore
    sandbox: GoogleCalendarSandbox


@final
class SandboxTokenSource:
    """Resolve the sandbox access token without touching the Keychain."""

    def __init__(self, access_token: str, secret_reference: str) -> None:
        """Bind the fixture's plaintext sandbox token and grant reference."""
        self._access_token: str = access_token
        self._secret_reference: str = secret_reference

    def access_token(self, grant: CalendarOAuthGrant) -> SecretStr:
        """Return the sandbox token only for the fixture's own grant."""
        if grant.secret_reference != self._secret_reference:
            message = "sandbox token source rejected a foreign grant"
            raise CalendarAuthorizationError(message)
        return SecretStr(self._access_token)


def build_fixture_runtime(
    fixture: TomorrowFixture,
    sandbox_path: Path,
    store: EncryptedStateStore,
) -> SliceRuntime:
    """Wire the deterministic fixture into a hermetic slice runtime."""
    sandbox_fixture = SandboxFixture.from_path(sandbox_path)
    sandbox = GoogleCalendarSandbox(
        valid_token=sandbox_fixture.access_token,
        primary_event_id=sandbox_fixture.primary_event_id,
    )
    config = CalendarAdapterConfig(
        owner_id=sandbox_fixture.owner_id,
        oauth_grant=CalendarOAuthGrant(
            secret_reference=sandbox_fixture.secret_reference,
            scopes=(GOOGLE_CALENDAR_APP_SCOPE,),
        ),
    )
    deps = CalendarAdapterDependencies(
        transport=sandbox,
        token_source=SandboxTokenSource(
            sandbox_fixture.access_token,
            sandbox_fixture.secret_reference,
        ),
        clock=fixture.clock,
    )
    calendar = GoogleCalendarAdapter(config, deps)
    interpreter = FixtureInterpretationAdapter(fixture.interpretations)
    devices = DeviceRegistry(fixture.clock, store.devices)
    if devices.device(DeviceId("iphone-1")) is None:
        _ = devices.enroll(
            DeviceId("iphone-1"),
            "fixture-iphone-1",
            ActorId("lifeos"),
            approval_public_key=FIXTURE_PUBLIC_KEY,
        )
    service = TomorrowPlanningSlice(
        clock=fixture.clock,
        store=store,
        interpreter=interpreter,
        calendar=calendar,
        devices=devices,
    )
    return SliceRuntime(service=service, store=store, sandbox=sandbox)
