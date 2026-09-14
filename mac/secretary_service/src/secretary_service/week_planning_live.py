"""Explicit live Calendar wiring for the Mac-hosted week-planning relay."""

import secrets
from collections.abc import Callable
from datetime import timedelta

from secretary_service.enrollment import DeviceRegistry
from secretary_service.google_calendar import GoogleCalendarAdapter
from secretary_service.google_calendar_contract import (
    DEFAULT_REFRESH_REFERENCE,
    GOOGLE_CALENDAR_APP_SCOPE,
    CalendarAdapterConfig,
    CalendarAdapterDependencies,
    CalendarOAuthGrant,
    GoogleRefreshTokenSource,
)
from secretary_service.google_calendar_live import (
    GoogleCalendarRESTTransport,
    HTTPSGoogleHTTPClient,
    KeychainCalendarIdStore,
)
from secretary_service.keys import KeyProvider
from secretary_service.storage import Clock, EncryptedStateStore
from secretary_service.week_planning import WeekPlanningService


def live_week_planning_factory(
    clock: Clock, keys: KeyProvider
) -> Callable[[EncryptedStateStore], WeekPlanningService]:
    """Build real provider boundaries without making calls or granting execution.

    The process-local lease key never leaves the Mac. Each approval still passes
    through durable device verification and consumption before a lease is issued.
    Restarting invalidates outstanding leases; it cannot resurrect approvals.
    """
    signing_key = secrets.token_bytes(32)
    calendar = GoogleCalendarAdapter(
        CalendarAdapterConfig(
            owner_id="lifeos",
            oauth_grant=CalendarOAuthGrant(
                secret_reference=DEFAULT_REFRESH_REFERENCE,
                scopes=(GOOGLE_CALENDAR_APP_SCOPE,),
            ),
        ),
        CalendarAdapterDependencies(
            transport=GoogleCalendarRESTTransport(
                KeychainCalendarIdStore(), HTTPSGoogleHTTPClient()
            ),
            token_source=GoogleRefreshTokenSource(keys),
            clock=clock,
        ),
    )

    def factory(store: EncryptedStateStore) -> WeekPlanningService:
        return WeekPlanningService(
            clock,
            DeviceRegistry(clock, store.devices),
            store,
            calendar,
            signing_key,
            timedelta(minutes=10),
        )

    return factory
