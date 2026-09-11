"""Deterministic event mapping, diffing, and bounded provider writes."""

import base64
import hashlib
from typing import final

from pydantic import SecretStr

from secretary_service.google_calendar_contract import (
    CalendarAdapterConfig,
    CalendarAdapterDependencies,
    CalendarDelete,
    CalendarDiff,
    CalendarEventDraft,
    CalendarOperation,
    CalendarOwnership,
    CalendarPlan,
    CalendarSyncState,
    CalendarWrite,
    RemoteCalendar,
    RemoteEvent,
)
from secretary_service.google_calendar_errors import (
    CalendarContractError,
    CalendarTransientError,
)


@final
class CalendarOperations:
    """Pure event mapping plus bounded provider mutation helpers."""

    def __init__(self, config: CalendarAdapterConfig, deps: CalendarAdapterDependencies) -> None:
        """Bind operation helpers to one adapter configuration."""
        self._config = config
        self._deps = deps

    def diff(
        self, plan: CalendarPlan, state: CalendarSyncState, *, applying: bool
    ) -> tuple[CalendarDiff, ...]:
        """Compare canonical plan events with synchronized provider events."""
        current = {event.event_id: event for event in state.events}
        calendar_id = state.calendar_id or "pending-app-calendar"
        operations: list[CalendarDiff] = []
        for draft in plan.events:
            desired = self.remote(plan, draft, calendar_id)
            found = current.get(desired.event_id)
            operations.append(
                CalendarDiff(
                    operation=self._operation(found, desired, applying=applying),
                    event_id=desired.event_id,
                    before_summary=None if found is None else found.summary,
                    after_summary=draft.summary,
                )
            )
        return tuple(operations)

    def remote(
        self, plan: CalendarPlan, draft: CalendarEventDraft, calendar: RemoteCalendar | str
    ) -> RemoteEvent:
        """Map one draft to deterministic Google identity and ownership tags."""
        calendar_id = calendar.calendar_id if isinstance(calendar, RemoteCalendar) else calendar
        digest = hashlib.sha256(f"{self._config.owner_id}\x1f{draft.event_key}".encode()).digest()
        event_id = "lifeos" + base64.b32hexencode(digest).decode().lower().rstrip("=")
        return RemoteEvent(
            calendar_id=calendar_id,
            event_id=event_id,
            summary=draft.summary,
            starts_at=draft.starts_at,
            ends_at=draft.ends_at,
            ownership=CalendarOwnership(
                owner_id=self._config.owner_id,
                proposal_id=str(plan.authorization.proposal_id),
                payload_hash=plan.authorization.payload_hash(),
                event_key=draft.event_key,
            ),
            revision="pending",
        )

    def write(self, token: SecretStr, calendar: RemoteCalendar, event: RemoteEvent) -> int:
        """Upsert with bounded retries and verify provider identity output."""
        request = CalendarWrite(calendar_id=calendar.calendar_id, event=event)
        for attempt in range(1, self._config.max_attempts + 1):
            try:
                result = self._deps.transport.write_event(token, request)
            except CalendarTransientError:
                if attempt == self._config.max_attempts:
                    raise
                continue
            identity_matches = (
                result.calendar_id == calendar.calendar_id
                and result.event_id == event.event_id
                and result.ownership == event.ownership
            )
            content_matches = (
                result.summary == event.summary
                and result.starts_at == event.starts_at
                and result.ends_at == event.ends_at
            )
            if not identity_matches or not content_matches:
                message = "provider returned mismatched calendar event identity or content"
                raise CalendarContractError(message)
            return attempt
        message = "unreachable retry loop"
        raise AssertionError(message)

    def delete(self, token: SecretStr, request: CalendarDelete) -> None:
        """Delete idempotently with bounded pre-mutation retries."""
        for attempt in range(1, self._config.max_attempts + 1):
            try:
                self._deps.transport.delete_event(token, request)
            except CalendarTransientError:
                if attempt == self._config.max_attempts:
                    raise
                continue
            return

    def _operation(
        self, found: RemoteEvent | None, desired: RemoteEvent, *, applying: bool
    ) -> CalendarOperation:
        if found is None:
            return CalendarOperation.INSERT if applying else CalendarOperation.MISSING
        ownership = found.ownership
        if ownership is None or ownership.owner_id != self._config.owner_id:
            return CalendarOperation.CONFLICT
        same_content = (
            found.summary == desired.summary
            and found.starts_at == desired.starts_at
            and found.ends_at == desired.ends_at
            and found.ownership == desired.ownership
        )
        if same_content:
            return CalendarOperation.NOOP
        desired_ownership = desired.ownership
        if desired_ownership is None:
            message = "generated event is missing ownership"
            raise CalendarContractError(message)
        if ownership.payload_hash == desired_ownership.payload_hash:
            return CalendarOperation.EXTERNAL_EDIT
        return CalendarOperation.UPDATE if applying else CalendarOperation.CONFLICT
