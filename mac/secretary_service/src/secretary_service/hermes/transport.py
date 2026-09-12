"""Fixed-origin HTTP transport for the isolated Hermes sidecar."""

import http.client
import json
from dataclasses import dataclass
from http import HTTPStatus
from typing import Final
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import JsonValue, SecretStr

from secretary_service.hermes.contracts import (
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    HermesEndpoint,
)
from secretary_service.slice.validation import InterpretationProviderError

MAX_TIMEOUT_SECONDS: Final = 60


@dataclass(frozen=True)
class HermesHTTPTransport:
    """Verify HTTPS; permit explicit loopback HTTP only inside a local test/sandbox.

    Origin belongs to trusted deployment configuration. No request can change
    host, follow a redirect, invoke a session/admin endpoint, or supply headers.
    """

    origin: str
    timeout_seconds: float = 30
    allow_loopback_http: bool = False

    def __post_init__(self) -> None:
        """Reject credentials, paths, queries and insecure remote origins."""
        parsed = urlsplit(self.origin)
        valid = (
            parsed.hostname is not None
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and parsed.path in {"", "/"}
            and (
                parsed.scheme == "https"
                or (
                    self.allow_loopback_http
                    and parsed.scheme == "http"
                    and parsed.hostname in {"127.0.0.1", "::1"}
                )
            )
            and 0 < self.timeout_seconds <= MAX_TIMEOUT_SECONDS
        )
        if not valid:
            message = "invalid Hermes origin or timeout"
            raise ValueError(message)
        _ = parsed.port

    def request(
        self,
        endpoint: HermesEndpoint,
        payload: dict[str, JsonValue] | None,
        credential: SecretStr,
        request_id: UUID,
    ) -> bytes:
        """Perform one bounded request, redacting failures and never retrying POST."""
        allowed = {"/v1/capabilities", "/v1/toolsets", "/v1/chat/completions"}
        if endpoint not in allowed or (endpoint == "/v1/chat/completions") != (payload is not None):
            message = "Hermes endpoint is not allowlisted for this operation"
            raise InterpretationProviderError(message)
        body = None if payload is None else json.dumps(payload).encode()
        if body is not None and len(body) > MAX_REQUEST_BYTES:
            message = "Hermes request exceeds size limit"
            raise InterpretationProviderError(message)
        parsed = urlsplit(self.origin)
        hostname = parsed.hostname
        if hostname is None:
            raise AssertionError
        connection = (
            http.client.HTTPSConnection(hostname, parsed.port, timeout=self.timeout_seconds)
            if parsed.scheme == "https"
            else http.client.HTTPConnection(hostname, parsed.port, timeout=self.timeout_seconds)
        )
        response: http.client.HTTPResponse | None = None
        try:
            connection.request(
                "GET" if payload is None else "POST",
                endpoint,
                body=body,
                headers={
                    "Authorization": f"Bearer {credential.get_secret_value()}",
                    "Content-Type": "application/json",
                    "X-Hermes-Session-Id": str(request_id),
                    "X-Hermes-Session-Key": str(request_id),
                },
            )
            response = connection.getresponse()
            if response.status != HTTPStatus.OK:
                message = f"Hermes returned HTTP {response.status}"
                raise InterpretationProviderError(message)
            data = response.read(MAX_RESPONSE_BYTES + 1)
            if len(data) > MAX_RESPONSE_BYTES:
                message = "Hermes response exceeds size limit"
                raise InterpretationProviderError(message)
        except (OSError, http.client.HTTPException, ValueError):
            message = "Hermes transport unavailable"
            raise InterpretationProviderError(message) from None
        else:
            return data
        finally:
            if response is not None:
                response.close()
            connection.close()
