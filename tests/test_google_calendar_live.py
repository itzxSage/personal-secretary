"""Live Calendar REST mapping remains hermetic through a scripted HTTP boundary."""

import json
from dataclasses import dataclass
from datetime import timedelta
from typing import cast, final

import pytest
from pydantic import SecretStr

from secretary_service.google_calendar_contract import (
    GOOGLE_CALENDAR_APP_SCOPE,
    LIFEOS_PROPOSED_CALENDAR,
    CalendarDelete,
    CalendarOAuthGrant,
    CalendarOwnership,
    CalendarWrite,
    GoogleRefreshTokenSource,
    RemoteEvent,
)
from secretary_service.google_calendar_errors import (
    CalendarAuthorizationError,
    CalendarContractError,
    CalendarInterruptedError,
    CalendarTransientError,
    StaleSyncTokenError,
)
from secretary_service.google_calendar_live import (
    GOOGLE_API_HOST,
    MAX_RESPONSE_BYTES,
    GoogleCalendarRESTTransport,
    GoogleHTTPResponse,
    HTTPSGoogleHTTPClient,
)
from secretary_service.keys import KeyUnavailableError
from tests.helpers import FakeClock

TOKEN = SecretStr("test-access-token")
CALENDAR_ID = "lifeos@example.test"
SYNC_CURSOR = "sync-2"
REFERENCE_FIXTURE = "refresh-ref"
CREDENTIAL_FIXTURE = "fixture-refresh-value"
SHORT_LIVED_FIXTURE = "short-lived-access-value"


@dataclass
class MemoryCalendarIds:
    value: str | None = None
    pending: bool = False

    def load(self) -> str | None:
        return self.value

    def save(self, calendar_id: str) -> None:
        self.value = calendar_id
        self.pending = False

    def clear(self) -> None:
        self.value = None

    def creation_pending(self) -> bool:
        return self.pending

    def mark_creation_pending(self) -> None:
        self.pending = True

    def clear_creation_pending(self) -> None:
        self.pending = False


@final
class ScriptedGoogleHTTP:
    def __init__(self, responses: list[GoogleHTTPResponse]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, str, bytes, str | None]] = []

    def request(
        self,
        method: str,
        target: str,
        token: SecretStr,
        body: bytes = b"",
        *,
        if_match: str | None = None,
    ) -> GoogleHTTPResponse:
        assert token.get_secret_value() == "test-access-token"
        self.requests.append((method, target, body, if_match))
        return self.responses.pop(0)


def response(status: int, value: object = None) -> GoogleHTTPResponse:
    return GoogleHTTPResponse(status, b"" if value is None else json.dumps(value).encode())


def ownership() -> CalendarOwnership:
    return CalendarOwnership(
        owner_id="owner", proposal_id="proposal", payload_hash="hash", event_key="workout"
    )


def remote(clock: FakeClock, *, revision: str = "pending") -> RemoteEvent:
    return RemoteEvent(
        calendar_id=CALENDAR_ID,
        event_id="lifeosabc",
        summary="Workout",
        starts_at=clock.now() + timedelta(hours=1),
        ends_at=clock.now() + timedelta(hours=2),
        ownership=ownership(),
        revision=revision,
    )


def event_json(event: RemoteEvent, *, revision: str = '"etag-1"') -> dict[str, object]:
    return {
        "id": event.event_id,
        "summary": event.summary,
        "start": {"dateTime": event.starts_at.isoformat()},
        "end": {"dateTime": event.ends_at.isoformat()},
        "etag": revision,
        "extendedProperties": {
            "private": {
                "lifeosOwner": "owner",
                "lifeosProposal": "proposal",
                "lifeosPayload": "hash",
                "lifeosEventKey": "workout",
            }
        },
    }


def test_calendar_identity_is_created_then_resolved_without_listing() -> None:
    ids = MemoryCalendarIds()
    http = ScriptedGoogleHTTP(
        [
            response(200, {"id": CALENDAR_ID, "summary": LIFEOS_PROPOSED_CALENDAR}),
            response(200, {"id": CALENDAR_ID, "summary": LIFEOS_PROPOSED_CALENDAR}),
        ]
    )
    transport = GoogleCalendarRESTTransport(ids, http)
    created = transport.create_calendar(TOKEN, LIFEOS_PROPOSED_CALENDAR)
    found = transport.find_calendar(TOKEN, LIFEOS_PROPOSED_CALENDAR)
    assert created == found
    assert ids.value == CALENDAR_ID
    assert not ids.pending
    assert [item[1] for item in http.requests] == [
        "/calendar/v3/calendars",
        "/calendar/v3/calendars/lifeos%40example.test",
    ]
    assert all("calendarList" not in item[1] and "primary" not in item[1] for item in http.requests)


def test_interrupted_calendar_creation_is_not_automatically_retried() -> None:
    @final
    class InterruptingHTTP:
        def request(
            self,
            method: str,
            target: str,
            token: SecretStr,
            body: bytes = b"",
            *,
            if_match: str | None = None,
        ) -> GoogleHTTPResponse:
            del method, target, token, body, if_match
            message = "interrupted"
            raise CalendarInterruptedError(message)

    ids = MemoryCalendarIds()
    transport = GoogleCalendarRESTTransport(ids, InterruptingHTTP())
    with pytest.raises(CalendarInterruptedError):
        _ = transport.create_calendar(TOKEN, LIFEOS_PROPOSED_CALENDAR)
    assert ids.pending
    with pytest.raises(CalendarContractError, match="manual reconciliation"):
        _ = transport.create_calendar(TOKEN, LIFEOS_PROPOSED_CALENDAR)


def test_paginated_sync_maps_ownership_and_requires_terminal_token(clock: FakeClock) -> None:
    event = remote(clock)
    http = ScriptedGoogleHTTP(
        [
            response(200, {"items": [event_json(event)], "nextPageToken": "page-2"}),
            response(
                200,
                {
                    "items": [{"id": "deleted-id", "status": "cancelled"}],
                    "nextSyncToken": SYNC_CURSOR,
                },
            ),
        ]
    )
    transport = GoogleCalendarRESTTransport(MemoryCalendarIds(CALENDAR_ID), http)
    page = transport.sync_events(TOKEN, CALENDAR_ID, None)
    assert page.full_sync
    assert page.next_sync_token == SYNC_CURSOR
    assert page.events[0].ownership == ownership()
    assert page.events[1].deleted
    assert "pageToken=page-2" in http.requests[1][1]


def test_stale_sync_and_provider_failures_are_typed() -> None:
    ids = MemoryCalendarIds(CALENDAR_ID)
    with pytest.raises(StaleSyncTokenError):
        _ = GoogleCalendarRESTTransport(ids, ScriptedGoogleHTTP([response(410)])).sync_events(
            TOKEN, CALENDAR_ID, "stale"
        )
    with pytest.raises(CalendarAuthorizationError):
        _ = GoogleCalendarRESTTransport(ids, ScriptedGoogleHTTP([response(401)])).find_calendar(
            TOKEN, LIFEOS_PROPOSED_CALENDAR
        )
    with pytest.raises(CalendarTransientError):
        _ = GoogleCalendarRESTTransport(ids, ScriptedGoogleHTTP([response(429)])).find_calendar(
            TOKEN, LIFEOS_PROPOSED_CALENDAR
        )


def test_insert_retry_reads_exact_committed_event(clock: FakeClock) -> None:
    event = remote(clock)
    http = ScriptedGoogleHTTP([response(409), response(200, event_json(event))])
    result = GoogleCalendarRESTTransport(MemoryCalendarIds(CALENDAR_ID), http).write_event(
        TOKEN, CalendarWrite(calendar_id=CALENDAR_ID, event=event)
    )
    assert result == event.model_copy(update={"revision": '"etag-1"'})
    assert [item[0] for item in http.requests] == ["POST", "GET"]
    body = cast("dict[str, object]", json.loads(http.requests[0][2]))
    extended = cast("dict[str, object]", body["extendedProperties"])
    private = cast("dict[str, object]", extended["private"])
    assert private["lifeosOwner"] == "owner"
    assert "test-access-token" not in repr(http.requests)


def test_update_uses_etag_and_concurrent_change_fails(clock: FakeClock) -> None:
    event = remote(clock, revision='"etag-before"')
    http = ScriptedGoogleHTTP([response(412)])
    with pytest.raises(CalendarContractError, match="concurrently"):
        _ = GoogleCalendarRESTTransport(MemoryCalendarIds(CALENDAR_ID), http).write_event(
            TOKEN, CalendarWrite(calendar_id=CALENDAR_ID, event=event)
        )
    assert http.requests[0][0] == "PUT"
    assert http.requests[0][3] == '"etag-before"'


def test_primary_and_foreign_calendar_mutations_are_rejected(clock: FakeClock) -> None:
    transport = GoogleCalendarRESTTransport(MemoryCalendarIds(CALENDAR_ID), ScriptedGoogleHTTP([]))
    with pytest.raises(CalendarContractError):
        _ = transport.write_event(TOKEN, CalendarWrite(calendar_id="primary", event=remote(clock)))
    with pytest.raises(CalendarContractError):
        transport.delete_event(TOKEN, CalendarDelete(calendar_id="foreign", event_id="event"))


def test_delete_is_idempotent_and_malformed_provider_content_is_redacted() -> None:
    ids = MemoryCalendarIds(CALENDAR_ID)
    http = ScriptedGoogleHTTP([response(404)])
    GoogleCalendarRESTTransport(ids, http).delete_event(
        TOKEN, CalendarDelete(calendar_id=CALENDAR_ID, event_id="event")
    )
    malformed = b'{"private":"do not echo"'
    with pytest.raises(CalendarContractError) as captured:
        _ = GoogleCalendarRESTTransport(
            ids, ScriptedGoogleHTTP([GoogleHTTPResponse(200, malformed)])
        ).find_calendar(TOKEN, LIFEOS_PROPOSED_CALENDAR)
    assert "do not echo" not in str(captured.value)


def test_https_client_fixes_host_path_and_keeps_token_out_of_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        status: int = 200

        def read(self, amount: int) -> bytes:
            assert amount == MAX_RESPONSE_BYTES + 1
            return b"{}"

        def getheader(self, name: str) -> str | None:
            return '"revision"' if name == "ETag" else None

    class FakeConnection:
        def __init__(self, host: str, **values: object) -> None:
            assert host == GOOGLE_API_HOST
            assert values["timeout"] == 30
            self.closed: bool = False
            self.sent: tuple[str, str, bytes, dict[str, str]] | None = None

        def request(self, method: str, target: str, body: bytes, headers: dict[str, str]) -> None:
            self.sent = (method, target, body, headers)

        def getresponse(self) -> FakeResponse:
            return FakeResponse()

        def close(self) -> None:
            self.closed = True

    connections: list[FakeConnection] = []

    def connection(host: str, **values: object) -> FakeConnection:
        result = FakeConnection(host, **values)
        connections.append(result)
        return result

    monkeypatch.setattr(
        "secretary_service.google_calendar_live.http.client.HTTPSConnection", connection
    )
    result = HTTPSGoogleHTTPClient().request(
        "PUT",
        "/calendar/v3/calendars/app/events/event",
        TOKEN,
        b"{}",
        if_match='"revision"',
    )
    assert result.etag == '"revision"'
    assert connections[0].closed
    sent = connections[0].sent
    assert sent is not None
    assert sent[1] == "/calendar/v3/calendars/app/events/event"
    assert SHORT_LIVED_FIXTURE not in sent[1]
    assert sent[3]["Authorization"] == "Bearer test-access-token"
    assert sent[3]["If-Match"] == '"revision"'
    with pytest.raises(CalendarContractError, match="invalid Google Calendar request target"):
        _ = HTTPSGoogleHTTPClient().request("GET", "https://evil.test/calendar/v3/", TOKEN)


@final
class OAuthFixtureKeys:
    def __init__(self) -> None:
        self.lookups: list[str] = []

    def database_key(self) -> bytes:
        return b"d" * 32

    def audit_key(self) -> bytes:
        return b"a" * 32

    def backup_wrapping_key(self) -> bytes:
        return b"b" * 32

    def connector_secret(self, reference: str) -> str:
        self.lookups.append(reference)
        values = {
            REFERENCE_FIXTURE: CREDENTIAL_FIXTURE,
            "google-calendar-client-id": "fixture-client-id",
            "google-calendar-client-secret": "fixture-client-secret",
        }
        try:
            return values[reference]
        except KeyError as error:
            raise KeyUnavailableError(reference) from error


def test_refresh_token_source_uses_grant_reference_and_caches_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @final
    class FakeCredentials:
        valid = False
        token: str | None = None

        def __init__(self, **values: object) -> None:
            assert values["refresh_token"] == CREDENTIAL_FIXTURE
            assert values["scopes"] == [GOOGLE_CALENDAR_APP_SCOPE]

        def refresh(self, _request: object) -> None:
            self.token = SHORT_LIVED_FIXTURE
            self.valid = True

    monkeypatch.setattr("secretary_service.google_calendar_contract.Credentials", FakeCredentials)
    keys = OAuthFixtureKeys()
    source = GoogleRefreshTokenSource(keys)
    grant = CalendarOAuthGrant(
        secret_reference=REFERENCE_FIXTURE, scopes=(GOOGLE_CALENDAR_APP_SCOPE,)
    )
    first = source.access_token(grant)
    second = source.access_token(grant)
    assert first.get_secret_value() == SHORT_LIVED_FIXTURE
    assert second.get_secret_value() == SHORT_LIVED_FIXTURE
    assert keys.lookups.count(REFERENCE_FIXTURE) == 1
    assert SHORT_LIVED_FIXTURE not in repr(source)
