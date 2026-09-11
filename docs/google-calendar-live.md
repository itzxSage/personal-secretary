# Google Calendar staging setup

The live adapter is implemented but remains disabled until you authorize a Google
account and approve an actual calendar proposal. It uses only
`https://www.googleapis.com/auth/calendar.app.created`. Google documents that
scope as permission to create secondary calendars and manage events on calendars
created by the app. It does not grant access to the primary calendar.

The transport does not call `calendarList.list`. It creates one secondary calendar
named `LifeOS Proposed`, stores its returned ID in macOS Keychain, and subsequently
uses `calendars.get` to validate that exact ID. Google currently lists the narrow
app-created scope for both calendar creation and metadata lookup:

- https://developers.google.com/workspace/calendar/api/v3/reference/calendars/insert
- https://developers.google.com/workspace/calendar/api/v3/reference/calendars/get
- https://developers.google.com/workspace/calendar/api/auth

Events use deterministic provider IDs and private ownership properties. Inserts
recover from ambiguous responses by reading the deterministic ID. Updates send
the synchronized ETag through `If-Match`, so a concurrent Google edit fails rather
than being overwritten. Incremental sync follows bounded pages, handles deleted
events, and falls back to a full sync after Google's `410 Gone` stale-token response.
The fixed-host client validates TLS, refuses alternate authorities and redirects,
bounds provider responses, and never places OAuth material in URLs or logs.

## What you must do

1. In Google Cloud Console, create or choose a project and enable Google Calendar API.
2. Configure the OAuth consent screen. While it remains in Testing, add your Google
   account as a test user. Google documents that refresh tokens for external apps
   in Testing can expire after seven days, so this is suitable for staging only.
3. Create an OAuth client of type **Desktop app** and download its JSON file outside
   this repository. Do not paste or commit the JSON.
4. Run the authorization command. It opens Google's consent page in your browser
   and receives the redirect only on `127.0.0.1` with a random local port:

```bash
uv run scripts/google_calendar_oauth.py \
  --client-secrets /absolute/private/path/client_secret.json
```

The refresh value and desktop client material are placed in macOS Keychain under
service `com.personal-secretary.service`. No access or refresh value is printed.
The short-lived access token is refreshed in memory using Google's maintained auth
library and is not persisted by LifeOS.

Validate OAuth without creating or changing a calendar:

```bash
uv run scripts/google_calendar_status.py
```

After reviewing the scope, explicitly create the empty dedicated secondary calendar:

```bash
uv run scripts/google_calendar_status.py --create-dedicated-calendar
```

That command creates no events. Event writes still require a payload-bound,
device-signed `calendar.apply` approval. Rollback is limited to events whose private
ownership properties match the applied proposal. Live sandbox-account validation,
revocation testing, and app/service wiring remain release requirements.

Calendar creation itself has no caller-supplied idempotency key. LifeOS therefore
sets a durable Keychain crash guard before the request. If the response is lost, it
refuses to retry automatically. Inspect Google Calendar, remove any orphaned
`LifeOS Proposed` calendar, then explicitly clear the guard and retry:

```bash
uv run scripts/google_calendar_status.py --clear-pending-creation
```

## Local verification

```bash
uv run pytest -q tests/test_google_calendar.py tests/test_google_calendar_live.py
```

The tests use scripted responses and no Google account. They cover calendar-ID
custody, absence of CalendarList/primary-calendar access, pagination, stale tokens,
revocation/error classification, deterministic insert recovery, ETag updates,
idempotent deletion, ownership metadata, and response redaction.
