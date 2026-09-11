"""Deterministic Google Calendar API sandbox for integration and manual QA."""

from datetime import UTC, datetime
from typing import final

from pydantic import SecretStr

from secretary_service.google_calendar_contract import (
    LIFEOS_PROPOSED_CALENDAR,
    CalendarDelete,
    CalendarSyncPage,
    CalendarWrite,
    RemoteCalendar,
    RemoteEvent,
)
from secretary_service.google_calendar_errors import (
    CalendarAuthorizationError,
    CalendarContractError,
    CalendarInterruptedError,
    CalendarTransientError,
    StaleSyncTokenError,
)


@final
class GoogleCalendarSandbox:
    """Mutable in-memory provider fixture with injected API failure modes."""

    def __init__(self, valid_token: str, primary_event_id: str) -> None:
        """Create an isolated account with one protected primary event."""
        self._valid_token = valid_token
        self._revoked = False
        self._proposed_calendar: RemoteCalendar | None = None
        self._events: dict[str, dict[str, RemoteEvent]] = {
            "primary": {
                primary_event_id: RemoteEvent(
                    calendar_id="primary",
                    event_id=primary_event_id,
                    summary="Protected primary event",
                    starts_at=datetime(2026, 1, 1, 9, tzinfo=UTC),
                    ends_at=datetime(2026, 1, 1, 10, tzinfo=UTC),
                    ownership=None,
                    revision="1",
                )
            }
        }
        self._changes: list[tuple[int, RemoteEvent]] = []
        self._generation = 0
        self._minimum_sync_token = 0
        self._write_failures = 0
        self._interrupt_after_write = False
        self._misreport_write = False
        self._mutation_count = 0

    @property
    def mutation_count(self) -> int:
        """Return all sandbox calendar and event mutations."""
        return self._mutation_count

    @property
    def proposed_calendar_id(self) -> str | None:
        """Return the app-created calendar identity when present."""
        calendar = self._proposed_calendar
        return None if calendar is None else calendar.calendar_id

    @property
    def primary_events(self) -> tuple[RemoteEvent, ...]:
        """Return protected primary-calendar events."""
        return tuple(self._events["primary"].values())

    @property
    def proposed_events(self) -> tuple[RemoteEvent, ...]:
        """Return current events in the LifeOS Proposed calendar."""
        calendar_id = self.proposed_calendar_id
        if calendar_id is None:
            return ()
        return tuple(sorted(self._events[calendar_id].values(), key=lambda event: event.event_id))

    def revoke_token(self) -> None:
        """Reject every subsequent API operation."""
        self._revoked = True

    def fail_next_writes(self, count: int) -> None:
        """Inject retryable failures before the next writes mutate state."""
        self._write_failures = count

    def interrupt_next_write_after_commit(self) -> None:
        """Inject an ambiguous response failure after one committed write."""
        self._interrupt_after_write = True

    def misreport_next_write(self) -> None:
        """Return a contradictory calendar identity after one write."""
        self._misreport_write = True

    def expire_sync_tokens(self) -> None:
        """Make tokens older than the current generation return stale."""
        self._minimum_sync_token = self._generation

    def external_edit(self, event_id: str, *, summary: str) -> None:
        """Simulate a user edit while preserving private ownership tags."""
        calendar_id = self.proposed_calendar_id
        if calendar_id is None:
            message = "proposed calendar does not exist"
            raise CalendarContractError(message)
        event = self._events[calendar_id][event_id]
        changed = event.model_copy(
            update={"summary": summary, "revision": str(int(event.revision) + 1)}
        )
        self._record_change(changed)

    def find_calendar(self, token: SecretStr, summary: str) -> RemoteCalendar | None:
        """Find the app-created calendar without creating it."""
        self._authorize(token)
        calendar = self._proposed_calendar
        if calendar is not None and summary == calendar.summary:
            return calendar
        return None

    def create_calendar(self, token: SecretStr, summary: str) -> RemoteCalendar:
        """Idempotently create the dedicated non-primary calendar."""
        self._authorize(token)
        if summary != LIFEOS_PROPOSED_CALENDAR:
            message = "sandbox only permits the LifeOS Proposed calendar"
            raise CalendarContractError(message)
        if self._proposed_calendar is None:
            self._proposed_calendar = RemoteCalendar(
                calendar_id="lifeos-proposed-sandbox",
                summary=summary,
                app_owned=True,
                primary=False,
            )
            self._events[self._proposed_calendar.calendar_id] = {}
            self._mutation_count += 1
        return self._proposed_calendar

    def sync_events(
        self, token: SecretStr, calendar_id: str, sync_token: str | None
    ) -> CalendarSyncPage:
        """Return full or incremental event changes."""
        self._authorize(token)
        self._require_proposed(calendar_id)
        if sync_token is None:
            return CalendarSyncPage(
                events=self.proposed_events,
                next_sync_token=str(self._generation),
                full_sync=True,
            )
        try:
            generation = int(sync_token)
        except ValueError as error:
            message = "sync token is malformed or stale"
            raise StaleSyncTokenError(message) from error
        if generation < self._minimum_sync_token or generation > self._generation:
            message = "sync token is stale"
            raise StaleSyncTokenError(message)
        changed = tuple(event for version, event in self._changes if version > generation)
        return CalendarSyncPage(
            events=changed,
            next_sync_token=str(self._generation),
            full_sync=False,
        )

    def write_event(self, token: SecretStr, request: CalendarWrite) -> RemoteEvent:
        """Upsert by deterministic event identity."""
        self._authorize(token)
        self._require_proposed(request.calendar_id)
        if self._write_failures > 0:
            self._write_failures -= 1
            message = "sandbox transient write failure"
            raise CalendarTransientError(message)
        existing = self._events[request.calendar_id].get(request.event.event_id)
        revision = 1 if existing is None else int(existing.revision) + 1
        stored = request.event.model_copy(update={"revision": str(revision)})
        self._record_change(stored)
        if self._interrupt_after_write:
            self._interrupt_after_write = False
            message = "sandbox response interrupted after commit"
            raise CalendarInterruptedError(message)
        if self._misreport_write:
            self._misreport_write = False
            return stored.model_copy(update={"calendar_id": "primary"})
        return stored

    def delete_event(self, token: SecretStr, request: CalendarDelete) -> None:
        """Idempotently delete an event from the dedicated calendar."""
        self._authorize(token)
        self._require_proposed(request.calendar_id)
        event = self._events[request.calendar_id].pop(request.event_id, None)
        if event is None:
            return
        tombstone = event.model_copy(
            update={"deleted": True, "revision": str(int(event.revision) + 1)}
        )
        self._generation += 1
        self._changes.append((self._generation, tombstone))
        self._mutation_count += 1

    def _record_change(self, event: RemoteEvent) -> None:
        self._events[event.calendar_id][event.event_id] = event
        self._generation += 1
        self._changes.append((self._generation, event))
        self._mutation_count += 1

    def _authorize(self, token: SecretStr) -> None:
        valid = token.get_secret_value() == self._valid_token and not self._revoked
        if not valid:
            message = "Google OAuth token is revoked or invalid"
            raise CalendarAuthorizationError(message)

    def _require_proposed(self, calendar_id: str) -> None:
        if calendar_id != self.proposed_calendar_id or calendar_id == "primary":
            message = "sandbox rejected primary or foreign calendar mutation"
            raise CalendarContractError(message)
