"""Fixed-host Google Calendar REST transport with least-scope calendar identity custody."""

import http.client
import json
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, final
from urllib.parse import quote, urlencode

import keyring
from keyring.errors import KeyringError, PasswordDeleteError
from pydantic import JsonValue, SecretStr, TypeAdapter

from secretary_service.google_calendar_contract import (
    LIFEOS_PROPOSED_CALENDAR,
    CalendarDelete,
    CalendarOwnership,
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

GOOGLE_API_HOST = "www.googleapis.com"
CREATION_PENDING_MARKER = "calendar-create-in-flight"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_SYNC_PAGES = 20
HTTP_NOT_FOUND = 404
HTTP_CONFLICT = 409
HTTP_GONE = 410
HTTP_PRECONDITION_FAILED = 412
HTTP_SERVER_ERROR_MIN = 500
HTTP_SERVER_ERROR_MAX = 599
HTTP_SUCCESS_MIN = 200
HTTP_SUCCESS_MAX = 299
JSON_VALUE: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


class CalendarIdStore(Protocol):
    """Persist only the app-created secondary calendar identifier."""

    def load(self) -> str | None:
        """Return the trusted calendar ID if one was previously created."""
        ...

    def save(self, calendar_id: str) -> None:
        """Persist the trusted calendar ID."""
        ...

    def clear(self) -> None:
        """Forget a calendar confirmed absent at Google."""
        ...

    def creation_pending(self) -> bool:
        """Return whether a create may have committed without a response."""
        ...

    def mark_creation_pending(self) -> None:
        """Persist a guard before the non-idempotent creation request."""
        ...

    def clear_creation_pending(self) -> None:
        """Clear the guard after success or explicit reconciliation."""
        ...


@dataclass(frozen=True, slots=True)
class KeychainCalendarIdStore:
    """Keep private provider metadata alongside connector secrets in Keychain."""

    service_name: str = "com.personal-secretary.service"
    account: str = "google-calendar-id"
    pending_account: str = "google-calendar-creation-pending"

    def load(self) -> str | None:
        """Read the dedicated calendar identity."""
        try:
            return keyring.get_password(self.service_name, self.account)
        except KeyringError as error:
            message = "Google Calendar identity is unavailable"
            raise CalendarAuthorizationError(message) from error

    def save(self, calendar_id: str) -> None:
        """Write a newly created non-primary calendar identity."""
        if not calendar_id or calendar_id == "primary":
            message = "refusing to store a primary or empty calendar identity"
            raise CalendarContractError(message)
        try:
            keyring.set_password(self.service_name, self.account, calendar_id)
            self.clear_creation_pending()
        except KeyringError as error:
            message = "Google Calendar identity could not be stored"
            raise CalendarAuthorizationError(message) from error

    def clear(self) -> None:
        """Remove a stale identity; a missing item is already clear."""
        try:
            keyring.delete_password(self.service_name, self.account)
        except PasswordDeleteError:
            return
        except KeyringError as error:
            message = "Google Calendar identity could not be cleared"
            raise CalendarAuthorizationError(message) from error

    def creation_pending(self) -> bool:
        """Read the crash guard without exposing calendar metadata."""
        try:
            return keyring.get_password(self.service_name, self.pending_account) is not None
        except KeyringError as error:
            message = "Google Calendar creation state is unavailable"
            raise CalendarAuthorizationError(message) from error

    def mark_creation_pending(self) -> None:
        """Set the crash guard before attempting calendar insertion."""
        try:
            keyring.set_password(self.service_name, self.pending_account, CREATION_PENDING_MARKER)
        except KeyringError as error:
            message = "Google Calendar creation state could not be stored"
            raise CalendarAuthorizationError(message) from error

    def clear_creation_pending(self) -> None:
        """Clear the guard while treating an absent item as success."""
        try:
            keyring.delete_password(self.service_name, self.pending_account)
        except PasswordDeleteError:
            return
        except KeyringError as error:
            message = "Google Calendar creation state could not be cleared"
            raise CalendarAuthorizationError(message) from error


@dataclass(frozen=True, slots=True)
class GoogleHTTPResponse:
    """Bounded response metadata without logging provider content."""

    status: int
    body: bytes
    etag: str | None = None


class GoogleHTTPClient(Protocol):
    """Injectable fixed-host HTTPS boundary."""

    def request(
        self,
        method: str,
        target: str,
        token: SecretStr,
        body: bytes = b"",
        *,
        if_match: str | None = None,
    ) -> GoogleHTTPResponse:
        """Send one request without redirects or implicit retries."""
        ...


@final
class HTTPSGoogleHTTPClient:
    """TLS-validating Google API client with a hard-coded authority."""

    def request(
        self,
        method: str,
        target: str,
        token: SecretStr,
        body: bytes = b"",
        *,
        if_match: str | None = None,
    ) -> GoogleHTTPResponse:
        """Send to Google only; close every connection and bound all response bytes."""
        if not target.startswith("/calendar/v3/") or "\r" in target or "\n" in target:
            message = "invalid Google Calendar request target"
            raise CalendarContractError(message)
        headers = {
            "Authorization": f"Bearer {token.get_secret_value()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if if_match is not None:
            headers["If-Match"] = if_match
        connection = http.client.HTTPSConnection(
            GOOGLE_API_HOST, timeout=30, context=ssl.create_default_context()
        )
        try:
            connection.request(method, target, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            if len(payload) > MAX_RESPONSE_BYTES:
                message = "Google Calendar response exceeded the size limit"
                raise CalendarContractError(message)
            return GoogleHTTPResponse(response.status, payload, response.getheader("ETag"))
        except (OSError, http.client.HTTPException) as error:
            message = "Google Calendar response was interrupted"
            raise CalendarInterruptedError(message) from error
        finally:
            connection.close()


@final
class GoogleCalendarRESTTransport:
    """Map the narrow calendar contract to Google Calendar API v3 resources."""

    def __init__(self, calendar_ids: CalendarIdStore, http: GoogleHTTPClient) -> None:
        """Bind trusted calendar identity custody to the fixed-host HTTP boundary."""
        self._calendar_ids = calendar_ids
        self._http = http

    def find_calendar(self, token: SecretStr, summary: str) -> RemoteCalendar | None:
        """Resolve only the locally retained app-created ID; never list other calendars."""
        calendar_id = self._calendar_ids.load()
        if calendar_id is None:
            return None
        response = self._http.request(
            "GET", f"/calendar/v3/calendars/{quote(calendar_id, safe='')}", token
        )
        if response.status == HTTP_NOT_FOUND:
            self._calendar_ids.clear()
            return None
        data = self._json(self._success(response))
        found_id = self._string(data, "id")
        found_summary = self._string(data, "summary")
        if found_id != calendar_id or found_summary != summary or found_id == "primary":
            message = "Google returned a mismatched calendar identity"
            raise CalendarContractError(message)
        return RemoteCalendar(
            calendar_id=found_id, summary=found_summary, app_owned=True, primary=False
        )

    def create_calendar(self, token: SecretStr, summary: str) -> RemoteCalendar:
        """Create exactly the dedicated secondary calendar and retain its returned ID."""
        if summary != LIFEOS_PROPOSED_CALENDAR:
            message = "only the LifeOS Proposed calendar may be created"
            raise CalendarContractError(message)
        if self._calendar_ids.creation_pending():
            message = "prior calendar creation needs manual reconciliation"
            raise CalendarContractError(message)
        self._calendar_ids.mark_creation_pending()
        response = self._http.request(
            "POST",
            "/calendar/v3/calendars",
            token,
            json.dumps({"summary": summary}, separators=(",", ":")).encode(),
        )
        data = self._json(self._success(response))
        calendar_id = self._string(data, "id")
        if calendar_id == "primary" or self._string(data, "summary") != summary:
            message = "Google created an unexpected calendar"
            raise CalendarContractError(message)
        self._calendar_ids.save(calendar_id)
        return RemoteCalendar(
            calendar_id=calendar_id, summary=summary, app_owned=True, primary=False
        )

    def sync_events(
        self, token: SecretStr, calendar_id: str, sync_token: str | None
    ) -> CalendarSyncPage:
        """Follow bounded pages and require Google's terminal incremental sync token."""
        self._require_calendar(calendar_id)
        events: list[RemoteEvent] = []
        page_token: str | None = None
        next_sync_token: str | None = None
        for _ in range(MAX_SYNC_PAGES):
            query = {"maxResults": "2500", "showDeleted": "true", "singleEvents": "true"}
            if sync_token is not None:
                query["syncToken"] = sync_token
            if page_token is not None:
                query["pageToken"] = page_token
            target = f"/calendar/v3/calendars/{quote(calendar_id, safe='')}/events?" + urlencode(
                query
            )
            response = self._http.request("GET", target, token)
            if response.status == HTTP_GONE:
                message = "Google Calendar sync token expired"
                raise StaleSyncTokenError(message)
            data = self._json(self._success(response))
            items = data.get("items", [])
            if not isinstance(items, list):
                message = "Google Calendar returned invalid event items"
                raise CalendarContractError(message)
            events.extend(self._event(item, calendar_id) for item in items)
            raw_page = data.get("nextPageToken")
            page_token = raw_page if isinstance(raw_page, str) and raw_page else None
            raw_sync = data.get("nextSyncToken")
            next_sync_token = raw_sync if isinstance(raw_sync, str) and raw_sync else None
            if page_token is None:
                break
        if page_token is not None or next_sync_token is None:
            message = "Google Calendar sync pagination was incomplete"
            raise CalendarContractError(message)
        return CalendarSyncPage(
            events=tuple(events),
            next_sync_token=next_sync_token,
            full_sync=sync_token is None,
        )

    def write_event(self, token: SecretStr, request: CalendarWrite) -> RemoteEvent:
        """Create by deterministic ID or conditionally update the exact synchronized revision."""
        self._require_calendar(request.calendar_id)
        event = request.event
        target = (
            f"/calendar/v3/calendars/{quote(request.calendar_id, safe='')}/events"
            f"/{quote(event.event_id, safe='')}"
        )
        body = self._event_body(event)
        if event.revision == "pending":
            collection = target.rsplit("/", 1)[0]
            response = self._http.request("POST", collection, token, body)
            if response.status == HTTP_CONFLICT:
                response = self._http.request("GET", target, token)
        else:
            response = self._http.request("PUT", target, token, body, if_match=event.revision)
        return self._event(self._json(self._success(response)), request.calendar_id)

    def delete_event(self, token: SecretStr, request: CalendarDelete) -> None:
        """Delete only from the retained calendar; missing is an idempotent success."""
        self._require_calendar(request.calendar_id)
        target = (
            f"/calendar/v3/calendars/{quote(request.calendar_id, safe='')}/events/"
            f"{quote(request.event_id, safe='')}"
        )
        response = self._http.request(
            "DELETE",
            target,
            token,
        )
        if response.status not in {204, 404}:
            _ = self._success(response)

    def _require_calendar(self, calendar_id: str) -> None:
        trusted = self._calendar_ids.load()
        if not calendar_id or calendar_id == "primary" or calendar_id != trusted:
            message = "refusing access outside the retained app-created calendar"
            raise CalendarContractError(message)

    @staticmethod
    def _success(response: GoogleHTTPResponse) -> bytes:
        if response.status in {401, 403}:
            message = "Google Calendar OAuth was rejected or revoked"
            raise CalendarAuthorizationError(message)
        if response.status in {408, 429} or (
            HTTP_SERVER_ERROR_MIN <= response.status <= HTTP_SERVER_ERROR_MAX
        ):
            message = "Google Calendar request failed transiently"
            raise CalendarTransientError(message)
        if response.status == HTTP_PRECONDITION_FAILED:
            message = "Google Calendar event changed concurrently"
            raise CalendarContractError(message)
        if not HTTP_SUCCESS_MIN <= response.status <= HTTP_SUCCESS_MAX:
            message = f"Google Calendar rejected the request ({response.status})"
            raise CalendarContractError(message)
        return response.body

    @staticmethod
    def _json(body: bytes) -> dict[str, JsonValue]:
        try:
            value = JSON_VALUE.validate_json(body)
        except ValueError as error:
            message = "Google Calendar returned malformed JSON"
            raise CalendarContractError(message) from error
        if not isinstance(value, dict):
            message = "Google Calendar returned an unexpected JSON value"
            raise CalendarContractError(message)
        return value

    @staticmethod
    def _string(data: dict[str, JsonValue], key: str) -> str:
        value = data.get(key)
        if not isinstance(value, str) or not value:
            message = f"Google Calendar response omitted {key}"
            raise CalendarContractError(message)
        return value

    def _event(self, raw: JsonValue, calendar_id: str) -> RemoteEvent:
        if not isinstance(raw, dict):
            message = "Google Calendar returned a malformed event"
            raise CalendarContractError(message)
        event_id = self._string(raw, "id")
        deleted = raw.get("status") == "cancelled"
        revision = raw.get("etag")
        if not isinstance(revision, str) or not revision:
            revision = "deleted" if deleted else "missing"
        if deleted:
            epoch = datetime(1970, 1, 1, tzinfo=UTC)
            return RemoteEvent(
                calendar_id=calendar_id,
                event_id=event_id,
                summary="deleted",
                starts_at=epoch,
                ends_at=epoch,
                ownership=None,
                revision=revision,
                deleted=True,
            )
        start = raw.get("start")
        end = raw.get("end")
        if not isinstance(start, dict) or not isinstance(end, dict):
            message = "Google Calendar event omitted boundaries"
            raise CalendarContractError(message)
        starts_at = datetime.fromisoformat(self._string(start, "dateTime"))
        ends_at = datetime.fromisoformat(self._string(end, "dateTime"))
        extended = raw.get("extendedProperties")
        private: JsonValue = extended.get("private") if isinstance(extended, dict) else None
        ownership = None
        if isinstance(private, dict):
            try:
                ownership = CalendarOwnership(
                    owner_id=self._string(private, "lifeosOwner"),
                    proposal_id=self._string(private, "lifeosProposal"),
                    payload_hash=self._string(private, "lifeosPayload"),
                    event_key=self._string(private, "lifeosEventKey"),
                )
            except CalendarContractError:
                ownership = None
        return RemoteEvent(
            calendar_id=calendar_id,
            event_id=event_id,
            summary=self._string(raw, "summary"),
            starts_at=starts_at,
            ends_at=ends_at,
            ownership=ownership,
            revision=revision,
        )

    @staticmethod
    def _event_body(event: RemoteEvent) -> bytes:
        ownership = event.ownership
        if ownership is None:
            message = "refusing to write an event without LifeOS ownership"
            raise CalendarContractError(message)
        value = {
            "id": event.event_id,
            "summary": event.summary,
            "start": {"dateTime": event.starts_at.isoformat()},
            "end": {"dateTime": event.ends_at.isoformat()},
            "extendedProperties": {
                "private": {
                    "lifeosOwner": ownership.owner_id,
                    "lifeosProposal": ownership.proposal_id,
                    "lifeosPayload": ownership.payload_hash,
                    "lifeosEventKey": ownership.event_key,
                }
            },
        }
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
