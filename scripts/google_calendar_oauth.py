#!/usr/bin/env python3
"""Authorize least-scope Google Calendar access and store OAuth material in Keychain."""

import argparse
import json
import sys
from pathlib import Path

import keyring
from google.auth.exceptions import GoogleAuthError
from google_auth_oauthlib.flow import InstalledAppFlow
from keyring.errors import KeyringError
from oauthlib.oauth2 import OAuth2Error

from secretary_service.google_calendar_contract import (
    DEFAULT_CLIENT_ID_REFERENCE,
    DEFAULT_CLIENT_SECRET_REFERENCE,
    DEFAULT_REFRESH_REFERENCE,
    GOOGLE_CALENDAR_APP_SCOPE,
)

SERVICE = "com.personal-secretary.service"


def main() -> int:
    """Open Google's consent page; secrets are never printed or written to project files."""
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--client-secrets", type=Path, required=True)
    args = parser.parse_args()
    try:
        raw = json.loads(args.client_secrets.read_text(encoding="utf-8"))
        installed = raw["installed"]
        client_id = str(installed["client_id"])
        client_secret = str(installed["client_secret"])
        flow = InstalledAppFlow.from_client_secrets_file(
            str(args.client_secrets), scopes=[GOOGLE_CALENDAR_APP_SCOPE]
        )
        credentials = flow.run_local_server(
            host="127.0.0.1",
            port=0,
            authorization_prompt_message="Open this local Google authorization URL: {url}",
            success_message="Calendar authorization received. You may close this tab.",
            open_browser=True,
        )
        if credentials.refresh_token is None:
            print(
                "Google returned no refresh token. Revoke the prior test grant and retry.",
                file=sys.stderr,
            )
            return 1
        granted = set(credentials.granted_scopes or credentials.scopes or ())
        if granted != {GOOGLE_CALENDAR_APP_SCOPE}:
            print("Google did not grant exactly the required calendar scope.", file=sys.stderr)
            return 1
        keyring.set_password(SERVICE, DEFAULT_CLIENT_ID_REFERENCE, client_id)
        keyring.set_password(SERVICE, DEFAULT_CLIENT_SECRET_REFERENCE, client_secret)
        keyring.set_password(SERVICE, DEFAULT_REFRESH_REFERENCE, str(credentials.refresh_token))
    except (
        OSError,
        KeyError,
        TypeError,
        ValueError,
        GoogleAuthError,
        OAuth2Error,
        KeyringError,
    ) as error:
        print(f"Calendar authorization setup failed: {type(error).__name__}", file=sys.stderr)
        return 1
    print("Google Calendar OAuth stored in Keychain. Calendar writes remain approval-gated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
