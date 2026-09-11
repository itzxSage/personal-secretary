#!/usr/bin/env python3
"""Validate Google OAuth and optionally create the dedicated LifeOS calendar."""

import argparse
import sys

from secretary_service.google_calendar_contract import (
    DEFAULT_REFRESH_REFERENCE,
    GOOGLE_CALENDAR_APP_SCOPE,
    LIFEOS_PROPOSED_CALENDAR,
    CalendarOAuthGrant,
    GoogleRefreshTokenSource,
)
from secretary_service.google_calendar_errors import CalendarError
from secretary_service.google_calendar_live import (
    GoogleCalendarRESTTransport,
    HTTPSGoogleHTTPClient,
    KeychainCalendarIdStore,
)
from secretary_service.keys import MacOSKeychainKeyProvider


def main() -> int:
    """Print content-free status; calendar creation requires an explicit flag."""
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument(
        "--create-dedicated-calendar",
        action="store_true",
        help="create the secondary LifeOS Proposed calendar when absent",
    )
    _ = parser.add_argument(
        "--clear-pending-creation",
        action="store_true",
        help="clear the crash guard only after manually removing any orphan calendar",
    )
    args = parser.parse_args()
    try:
        grant = CalendarOAuthGrant(
            secret_reference=DEFAULT_REFRESH_REFERENCE,
            scopes=(GOOGLE_CALENDAR_APP_SCOPE,),
        )
        token = GoogleRefreshTokenSource(MacOSKeychainKeyProvider()).access_token(grant)
        calendar_ids = KeychainCalendarIdStore()
        if args.clear_pending_creation:
            calendar_ids.clear_creation_pending()
        transport = GoogleCalendarRESTTransport(calendar_ids, HTTPSGoogleHTTPClient())
        calendar = transport.find_calendar(token, LIFEOS_PROPOSED_CALENDAR)
        if calendar is None and args.create_dedicated_calendar:
            calendar = transport.create_calendar(token, LIFEOS_PROPOSED_CALENDAR)
    except CalendarError as error:
        print(f"Calendar status failed: {type(error).__name__}", file=sys.stderr)
        return 1
    if calendar is None:
        print("OAuth refresh succeeded; dedicated calendar not created.")
    else:
        print("OAuth and dedicated app-created calendar verified.")
    print("No calendar event was written. Production capabilities remain disabled.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
