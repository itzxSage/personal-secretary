"""Proposal-first Google Calendar synchronization and mutation adapter."""

from typing import final

from pydantic import SecretStr

from secretary_service.authority import ProposalState, Rollback
from secretary_service.google_calendar_contract import (
    GOOGLE_CALENDAR_APP_SCOPE,
    LIFEOS_PROPOSED_CALENDAR,
    CalendarAdapterConfig,
    CalendarAdapterDependencies,
    CalendarApplyResult,
    CalendarDelete,
    CalendarDiff,
    CalendarEventDraft,
    CalendarOAuthGrant,
    CalendarOperation,
    CalendarPlan,
    CalendarPreview,
    CalendarReconciliation,
    CalendarRollbackResult,
    CalendarSyncState,
    GoogleRefreshTokenSource,
    KeychainOAuthTokenSource,
    RemoteCalendar,
    calendar_payload,
)
from secretary_service.google_calendar_errors import (
    CalendarAuthorizationError,
    CalendarContractError,
    StaleSyncTokenError,
)
from secretary_service.google_calendar_operations import CalendarOperations

__all__ = [
    "GOOGLE_CALENDAR_APP_SCOPE",
    "CalendarAdapterConfig",
    "CalendarAdapterDependencies",
    "CalendarAuthorizationError",
    "CalendarContractError",
    "CalendarEventDraft",
    "CalendarOAuthGrant",
    "CalendarOperation",
    "CalendarPlan",
    "GoogleCalendarAdapter",
    "GoogleRefreshTokenSource",
    "KeychainOAuthTokenSource",
    "calendar_payload",
]


@final
class GoogleCalendarAdapter:
    """Apply approved LifeOS events only to an app-created calendar."""

    def __init__(self, config: CalendarAdapterConfig, deps: CalendarAdapterDependencies) -> None:
        """Create an adapter from immutable configuration and injected boundaries."""
        self._config = config
        self._deps = deps
        self._operations = CalendarOperations(config, deps)

    def dry_run(self, plan: CalendarPlan) -> CalendarPreview:
        """Preview the exact provider operations without mutating a calendar."""
        state = self.sync(None)
        return CalendarPreview(
            calendar_summary=LIFEOS_PROPOSED_CALENDAR,
            operations=self._operations.diff(plan, state, applying=True),
        )

    def apply(self, plan: CalendarPlan) -> CalendarApplyResult:
        """Apply an approved payload idempotently to the dedicated calendar."""
        if plan.authorization.state != ProposalState.APPROVED:
            message = "calendar apply requires an approved proposal"
            raise CalendarAuthorizationError(message)
        token = self._token()
        calendar = self._deps.transport.find_calendar(token, LIFEOS_PROPOSED_CALENDAR)
        if calendar is None:
            calendar = self._deps.transport.create_calendar(token, LIFEOS_PROPOSED_CALENDAR)
        self._validate_calendar(calendar)
        state = self.sync(None)
        current = {event.event_id: event for event in state.events}
        operations = self._operations.diff(plan, state, applying=True)
        attempts = 0
        for operation, draft in zip(operations, plan.events, strict=True):
            match operation.operation:
                case CalendarOperation.INSERT | CalendarOperation.UPDATE:
                    event = self._operations.remote(plan, draft, calendar)
                    if operation.operation == CalendarOperation.UPDATE:
                        expected = current.get(event.event_id)
                        if expected is None:
                            message = "calendar update lost its synchronized event"
                            raise CalendarContractError(message)
                        event = event.model_copy(update={"revision": expected.revision})
                    attempts += self._operations.write(token, calendar, event)
                case CalendarOperation.NOOP:
                    continue
                case CalendarOperation.EXTERNAL_EDIT | CalendarOperation.CONFLICT:
                    message = f"refusing to overwrite {operation.operation.value}"
                    raise CalendarContractError(message)
                case CalendarOperation.DELETE | CalendarOperation.MISSING:
                    message = f"invalid apply operation: {operation.operation.value}"
                    raise CalendarContractError(message)
        return CalendarApplyResult(
            operations=operations,
            sync_state=self.sync(None),
            attempts=attempts,
        )

    def sync(self, previous: CalendarSyncState | None) -> CalendarSyncState:
        """Perform incremental sync, falling back to full sync for stale tokens."""
        token = self._token()
        calendar = self._deps.transport.find_calendar(token, LIFEOS_PROPOSED_CALENDAR)
        if calendar is None:
            return CalendarSyncState(calendar_id=None, next_sync_token=None, events=())
        self._validate_calendar(calendar)
        sync_token = previous.next_sync_token if previous is not None else None
        try:
            page = self._deps.transport.sync_events(token, calendar.calendar_id, sync_token)
        except StaleSyncTokenError:
            page = self._deps.transport.sync_events(token, calendar.calendar_id, None)
        events = (
            {}
            if page.full_sync or previous is None
            else {event.event_id: event for event in previous.events}
        )
        for event in page.events:
            if event.deleted:
                _ = events.pop(event.event_id, None)
            else:
                events[event.event_id] = event
        return CalendarSyncState(
            calendar_id=calendar.calendar_id,
            next_sync_token=page.next_sync_token,
            events=tuple(sorted(events.values(), key=lambda item: item.event_id)),
        )

    def reconcile(
        self, plan: CalendarPlan, previous: CalendarSyncState | None
    ) -> CalendarReconciliation:
        """Report missing or externally edited owned events without mutation."""
        state = self.sync(previous)
        return CalendarReconciliation(
            operations=self._operations.diff(plan, state, applying=False),
            sync_state=state,
        )

    def rollback(self, plan: CalendarPlan, rollback: Rollback) -> CalendarRollbackResult:
        """Delete only events owned by the payload-bound applied proposal."""
        self._validate_rollback(plan, rollback)
        token = self._token()
        state = self.sync(None)
        operations: list[CalendarDiff] = []
        for event in state.events:
            ownership = event.ownership
            if ownership is None or ownership.owner_id != self._config.owner_id:
                continue
            if ownership.proposal_id != str(plan.authorization.proposal_id):
                continue
            if ownership.payload_hash != plan.authorization.payload_hash():
                continue
            self._operations.delete(
                token,
                CalendarDelete(calendar_id=event.calendar_id, event_id=event.event_id),
            )
            operations.append(
                CalendarDiff(
                    operation=CalendarOperation.DELETE,
                    event_id=event.event_id,
                    before_summary=event.summary,
                )
            )
        return CalendarRollbackResult(
            operations=tuple(operations),
            sync_state=self.sync(None),
        )

    def _token(self) -> SecretStr:
        return self._deps.token_source.access_token(self._config.oauth_grant)

    @staticmethod
    def _validate_calendar(calendar: RemoteCalendar) -> None:
        valid = (
            calendar.summary == LIFEOS_PROPOSED_CALENDAR
            and calendar.app_owned
            and not calendar.primary
        )
        if not valid:
            message = "provider returned a non-owned, primary, or mismatched calendar"
            raise CalendarContractError(message)

    def _validate_rollback(self, plan: CalendarPlan, rollback: Rollback) -> None:
        if plan.authorization.state != ProposalState.APPLIED:
            message = "calendar rollback requires an applied proposal"
            raise CalendarAuthorizationError(message)
        valid_binding = (
            rollback.proposal_id == plan.authorization.proposal_id
            and rollback.payload_hash == plan.authorization.payload_hash()
        )
        if not valid_binding or rollback.expires_at <= self._deps.clock.now():
            message = "calendar rollback authorization is stale or mismatched"
            raise CalendarAuthorizationError(message)
