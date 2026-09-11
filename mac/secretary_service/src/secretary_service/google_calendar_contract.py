"""Typed Google Calendar boundary records and transport protocols."""

import json
import threading
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Final, Protocol, Self, cast, final

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from pydantic import Field, SecretStr, StringConstraints, model_validator

from secretary_service.authority import ProposalRecord
from secretary_service.google_calendar_errors import CalendarAuthorizationError
from secretary_service.keys import KeyProvider, KeyUnavailableError
from secretary_service.models import FrozenModel, NonEmpty
from secretary_service.storage import Clock

if TYPE_CHECKING:
    from collections.abc import Callable

GOOGLE_CALENDAR_APP_SCOPE: Final = "https://www.googleapis.com/auth/calendar.app.created"
LIFEOS_PROPOSED_CALENDAR: Final = "LifeOS Proposed"
GOOGLE_TOKEN_URI: Final = "https://oauth2.googleapis.com/token"  # noqa: S105 - public endpoint
DEFAULT_REFRESH_REFERENCE: Final = "google-calendar-refresh-token"
DEFAULT_CLIENT_ID_REFERENCE: Final = "google-calendar-client-id"
DEFAULT_CLIENT_SECRET_REFERENCE: Final = "google-calendar-client-secret"  # noqa: S105 - key name
EventKey = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$"),
]


class CalendarOAuthGrant(FrozenModel):
    """Opaque Keychain reference and exact least-scope OAuth grant."""

    secret_reference: NonEmpty
    scopes: tuple[NonEmpty, ...]

    @model_validator(mode="after")
    def require_least_scope(self) -> Self:
        """Reject grants broader than app-created calendar access."""
        if self.scopes != (GOOGLE_CALENDAR_APP_SCOPE,):
            message = "Google Calendar grant must use only the least-scope app-created scope"
            raise ValueError(message)
        return self


class CalendarEventDraft(FrozenModel):
    """Canonical event projection accepted at the adapter boundary."""

    event_key: EventKey
    summary: NonEmpty
    starts_at: datetime
    ends_at: datetime

    @model_validator(mode="after")
    def require_chronological_aware_times(self) -> Self:
        """Reject naive or non-chronological event boundaries."""
        aware = self.starts_at.tzinfo is not None and self.ends_at.tzinfo is not None
        if not aware or self.ends_at <= self.starts_at:
            message = "calendar event times must be timezone-aware and chronological"
            raise ValueError(message)
        return self


def calendar_payload(events: tuple[CalendarEventDraft, ...]) -> str:
    """Return deterministic JSON bound into the authority proposal hash."""
    data = [event.model_dump(mode="json") for event in events]
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


class CalendarPlan(FrozenModel):
    """Calendar projection cryptographically bound to an authority proposal."""

    authorization: ProposalRecord
    events: tuple[CalendarEventDraft, ...]

    @model_validator(mode="after")
    def require_payload_binding(self) -> Self:
        """Reject projections that differ from the approved payload."""
        if self.authorization.action_class != "calendar.apply":
            message = "calendar plan requires calendar.apply authority"
            raise ValueError(message)
        if self.authorization.payload != calendar_payload(self.events):
            message = "calendar plan events do not match the authorized payload"
            raise ValueError(message)
        return self


class CalendarOwnership(FrozenModel):
    """Private extended properties proving LifeOS event ownership."""

    owner_id: NonEmpty
    proposal_id: NonEmpty
    payload_hash: NonEmpty
    event_key: EventKey


class RemoteCalendar(FrozenModel):
    """Provider calendar identity and ownership facts."""

    calendar_id: NonEmpty
    summary: NonEmpty
    app_owned: bool
    primary: bool


class RemoteEvent(FrozenModel):
    """Provider event projection with private ownership metadata."""

    calendar_id: NonEmpty
    event_id: NonEmpty
    summary: NonEmpty
    starts_at: datetime
    ends_at: datetime
    ownership: CalendarOwnership | None
    revision: NonEmpty
    deleted: bool = False


class CalendarSyncPage(FrozenModel):
    """Full or incremental sync response."""

    events: tuple[RemoteEvent, ...]
    next_sync_token: NonEmpty
    full_sync: bool


class CalendarWrite(FrozenModel):
    """Event upsert confined to one calendar."""

    calendar_id: NonEmpty
    event: RemoteEvent


class CalendarDelete(FrozenModel):
    """Event deletion confined to one calendar."""

    calendar_id: NonEmpty
    event_id: NonEmpty


class GoogleCalendarTransport(Protocol):
    """Narrow API seam implemented by sandbox and live transports."""

    def find_calendar(self, token: SecretStr, summary: str) -> RemoteCalendar | None:
        """Find an app-created calendar without mutation."""
        ...

    def create_calendar(self, token: SecretStr, summary: str) -> RemoteCalendar:
        """Create an app-owned secondary calendar."""
        ...

    def sync_events(
        self, token: SecretStr, calendar_id: str, sync_token: str | None
    ) -> CalendarSyncPage:
        """Return full or incremental event changes."""
        ...

    def write_event(self, token: SecretStr, request: CalendarWrite) -> RemoteEvent:
        """Upsert one event by deterministic provider identity."""
        ...

    def delete_event(self, token: SecretStr, request: CalendarDelete) -> None:
        """Delete one event from an app-created calendar."""
        ...


class OAuthTokenSource(Protocol):
    """Credential broker exposing OAuth only at the provider boundary."""

    def access_token(self, grant: CalendarOAuthGrant) -> SecretStr:
        """Resolve a grant into a redacted access token."""
        ...


@dataclass(frozen=True, slots=True)
class KeychainOAuthTokenSource:
    """Resolve OAuth material only when making a provider request."""

    key_provider: KeyProvider

    def access_token(self, grant: CalendarOAuthGrant) -> SecretStr:
        """Resolve the opaque Keychain reference into a redacted token."""
        try:
            return SecretStr(self.key_provider.connector_secret(grant.secret_reference))
        except KeyUnavailableError as error:
            message = "OAuth token is unavailable"
            raise CalendarAuthorizationError(message) from error


@final
class GoogleRefreshTokenSource:
    """Refresh a least-scope Google access token from Keychain-held OAuth material."""

    def __init__(
        self,
        key_provider: KeyProvider,
        *,
        client_id_reference: str = DEFAULT_CLIENT_ID_REFERENCE,
        client_secret_reference: str = DEFAULT_CLIENT_SECRET_REFERENCE,
    ) -> None:
        """Retain only opaque references; OAuth values remain in Keychain."""
        self._key_provider = key_provider
        self._client_id_reference = client_id_reference
        self._client_secret_reference = client_secret_reference
        self._credentials: Credentials | None = None
        self._lock = threading.Lock()

    def access_token(self, grant: CalendarOAuthGrant) -> SecretStr:
        """Return a cached access token or refresh it without persisting the short-lived token."""
        if grant.scopes != (GOOGLE_CALENDAR_APP_SCOPE,):
            message = "Google Calendar OAuth scope is broader than authorized"
            raise CalendarAuthorizationError(message)
        with self._lock:
            try:
                credentials = self._credentials or Credentials(
                    token=None,
                    refresh_token=self._key_provider.connector_secret(grant.secret_reference),
                    token_uri=GOOGLE_TOKEN_URI,
                    client_id=self._key_provider.connector_secret(self._client_id_reference),
                    client_secret=self._key_provider.connector_secret(
                        self._client_secret_reference
                    ),
                    scopes=list(grant.scopes),
                )
                if not credentials.valid:
                    cast("Callable[[object], None]", credentials.refresh)(Request())
                token = cast("object", credentials.token)
            except (GoogleAuthError, KeyUnavailableError, OSError) as error:
                message = "Google Calendar OAuth refresh failed or was revoked"
                raise CalendarAuthorizationError(message) from error
            if not isinstance(token, str) or not token:
                message = "Google Calendar OAuth refresh returned no access token"
                raise CalendarAuthorizationError(message)
            self._credentials = credentials
            return SecretStr(token)


class CalendarOperation(StrEnum):
    """Observable event-level proposal or reconciliation action."""

    INSERT = "insert"
    UPDATE = "update"
    NOOP = "noop"
    DELETE = "delete"
    EXTERNAL_EDIT = "external_edit"
    MISSING = "missing"
    CONFLICT = "conflict"


class CalendarDiff(FrozenModel):
    """Content-minimized event difference."""

    operation: CalendarOperation
    event_id: NonEmpty
    before_summary: str | None = None
    after_summary: str | None = None


class CalendarSyncState(FrozenModel):
    """Caller-persistable incremental synchronization state."""

    calendar_id: str | None
    next_sync_token: str | None
    events: tuple[RemoteEvent, ...]


class CalendarPreview(FrozenModel):
    """Non-mutating calendar proposal preview."""

    calendar_summary: NonEmpty
    operations: tuple[CalendarDiff, ...]


class CalendarApplyResult(FrozenModel):
    """Verified apply result with retry and sync evidence."""

    operations: tuple[CalendarDiff, ...]
    sync_state: CalendarSyncState
    attempts: int = Field(ge=0)


class CalendarReconciliation(FrozenModel):
    """External drift comparison without silent overwrite."""

    operations: tuple[CalendarDiff, ...]
    sync_state: CalendarSyncState


class CalendarRollbackResult(FrozenModel):
    """Owned event deletion result."""

    operations: tuple[CalendarDiff, ...]
    sync_state: CalendarSyncState


class CalendarAdapterConfig(FrozenModel):
    """Adapter ownership, OAuth, and bounded retry policy."""

    owner_id: NonEmpty
    oauth_grant: CalendarOAuthGrant
    max_attempts: int = Field(default=3, ge=1, le=5)


@dataclass(frozen=True, slots=True)
class CalendarAdapterDependencies:
    """Injected provider, credential broker, and deterministic clock."""

    transport: GoogleCalendarTransport
    token_source: OAuthTokenSource
    clock: Clock
