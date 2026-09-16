"""Provider-agnostic free-tier LLM interpretation adapter with zero paid API calls.

Implements the ``InterpretationAdapter`` protocol shape (``interpret(text,
source_event_id)``) against any OpenAI-compatible ``/v1/chat/completions``
endpoint. The provider is selected entirely by configuration: ``base_url``
names the free-tier host and ``model`` names the free model. No paid provider
endpoint is hardcoded and no API key is stored in code (Keychain by reference).

Consent and retention are re-verified before every request; no Keychain lookup
or network call happens without a current grant. HTTP 429 responses and
timeouts are retried with exponential backoff (3 attempts, capped at 30s).
Provider-down failures return an ``ErrorResponse`` proposal with a
``provider_unavailable`` status instead of raising.
"""

import http.client
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from typing import ClassVar, Literal, Protocol
from urllib.parse import urlparse
from uuid import UUID, uuid4

from pydantic import ConfigDict, JsonValue, SecretStr, TypeAdapter, ValidationError

from secretary_service.keys import KeyProvider
from secretary_service.models import FrozenModel, RecordId
from secretary_service.planner_models import DayPlanRequest
from secretary_service.slice.models import InterpretationProposal
from secretary_service.slice.validation import (
    InterpretationProviderError,
    preserve_plan_constraints,
)
from secretary_service.storage import Clock
from secretary_service.voice.privacy import RetentionVerification

MAX_INPUT_CHARACTERS = 16_000
MAX_RESPONSE_BYTES = 2_000_000
MAX_RETRIES = 3
MAX_BACKOFF_SECONDS = 30.0


class ConsentNotGrantedError(InterpretationProviderError):
    """Consent or retention prerequisite is not currently satisfied."""


class RetryableProviderError(InterpretationProviderError):
    """Provider failure that may succeed on a later attempt."""


class RateLimitError(RetryableProviderError):
    """Provider returned HTTP 429; safe to retry with backoff."""


class ProviderTimeoutError(RetryableProviderError):
    """Provider request timed out; safe to retry."""


class ErrorResponse(FrozenModel):
    """Error proposal returned instead of a plan when the provider fails."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    status: Literal["provider_unavailable", "rate_limited"]
    error: str


class ChatMessage(FrozenModel):
    """One assistant message in a chat-completions reply."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")

    role: str = ""
    content: str = ""


class ChatChoice(FrozenModel):
    """One chat-completions choice; only the assistant message is relevant."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")

    message: ChatMessage = ChatMessage()


class ChatCompletionsEnvelope(FrozenModel):
    """Provider status and output, deliberately excluding raw error bodies."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")

    choices: tuple[ChatChoice, ...] = ()


class FreeModelTransport(Protocol):
    """Single-request transport for a free-tier chat-completions endpoint."""

    def chat_completions(self, payload: dict[str, JsonValue], api_key: SecretStr) -> bytes:
        """Send one request; raise InterpretationProviderError on failure."""
        ...


@dataclass(frozen=True)
class FreeModelSettings:
    """Explicit free-tier model selection with consent and retention prerequisites."""

    model: str
    project_id: str
    key_reference: str
    retention: RetentionVerification
    consent_granted: Callable[[], bool]


def _parse_endpoint(base_url: str) -> tuple[str, int | None, bool]:
    """Split a base URL into host, port and TLS flag."""
    parsed = urlparse(base_url)
    host = parsed.hostname or ""
    if not host:
        message = "free model base URL is invalid"
        raise InterpretationProviderError(message)
    return host, parsed.port, parsed.scheme == "https"


def _connect(
    host: str, port: int | None, *, tls: bool, timeout_seconds: float
) -> http.client.HTTPConnection:
    """Open a TLS-verified or plain HTTP connection to the configured host."""
    if tls:
        return http.client.HTTPSConnection(host, port, timeout=timeout_seconds)
    return http.client.HTTPConnection(host, port, timeout=timeout_seconds)


def _authorization_headers(api_key: SecretStr) -> dict[str, str]:
    """Build request headers; the bearer token is optional for keyless tiers."""
    headers = {"Content-Type": "application/json"}
    secret_value = api_key.get_secret_value()
    if secret_value:
        headers["Authorization"] = f"Bearer {secret_value}"
    return headers


def _read_response(response: http.client.HTTPResponse) -> bytes:
    """Validate status and bound the response body."""
    if response.status == HTTPStatus.TOO_MANY_REQUESTS:
        message = "free model provider rate limited"
        raise RateLimitError(message)
    if response.status != HTTPStatus.OK:
        message = f"free model provider returned HTTP {response.status}"
        raise InterpretationProviderError(message)
    body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        message = "free model provider response exceeds size limit"
        raise InterpretationProviderError(message)
    return body


@dataclass(frozen=True)
class HTTPFreeModelTransport:
    """TLS-verified HTTP transport for an OpenAI-compatible chat-completions endpoint."""

    base_url: str
    timeout_seconds: float = 30

    def chat_completions(self, payload: dict[str, JsonValue], api_key: SecretStr) -> bytes:
        """POST to /v1/chat/completions on the configured base URL."""
        host, port, tls = _parse_endpoint(self.base_url)
        connection = _connect(host, port, tls=tls, timeout_seconds=self.timeout_seconds)
        try:
            connection.request(
                "POST",
                "/v1/chat/completions",
                body=json.dumps(payload).encode("utf-8"),
                headers=_authorization_headers(api_key),
            )
            return _read_response(connection.getresponse())
        except TimeoutError:
            message = "free model provider request timed out"
            raise ProviderTimeoutError(message) from None
        except (OSError, http.client.HTTPException):
            message = "free model provider is unavailable"
            raise InterpretationProviderError(message) from None
        finally:
            connection.close()


@dataclass(frozen=True)
class FreeModelAdapter:
    """Free-tier LLM interpretation adapter implementing the InterpretationAdapter protocol."""

    settings: FreeModelSettings
    clock: Clock
    keys: KeyProvider
    transport: FreeModelTransport
    base_plan: DayPlanRequest

    def _check_consent_and_retention(self) -> None:
        """Verify consent and retention before any Keychain lookup or network call."""
        if not self.settings.consent_granted():
            message = "interpretation consent is not granted"
            raise ConsentNotGrantedError(message)
        if not self.settings.retention.authorizes(
            self.settings.project_id,
            self.clock.now(),
        ):
            message = "interpretation retention verification is not current"
            raise ConsentNotGrantedError(message)

    def _request_with_retry(self, payload: dict[str, JsonValue], credential: SecretStr) -> bytes:
        """Send the request with exponential backoff on retryable failures."""
        for attempt in range(MAX_RETRIES):
            try:
                return self.transport.chat_completions(payload, credential)
            except RetryableProviderError:
                if attempt == MAX_RETRIES - 1:
                    raise
                delay = min(2.0**attempt, MAX_BACKOFF_SECONDS)
                time.sleep(delay)
        message = "free model provider retry limit exceeded"
        raise RetryableProviderError(message)

    def _parse_plan(self, response_body: bytes) -> DayPlanRequest:
        """Validate one chat-completions reply into a canonical day plan."""
        try:
            envelope = ChatCompletionsEnvelope.model_validate_json(response_body)
            if len(envelope.choices) != 1:
                message = "free model provider returned unexpected response structure"
                raise InterpretationProviderError(message)
            content = envelope.choices[0].message.content
            if not content:
                message = "free model provider returned empty content"
                raise InterpretationProviderError(message)
            return DayPlanRequest.model_validate_json(content)
        except (ValidationError, json.JSONDecodeError):
            message = "free model response failed planner validation"
            raise InterpretationProviderError(message) from None

    def interpret(self, text: str, source_event_id: UUID) -> InterpretationProposal | ErrorResponse:
        """Check consent, call the free-tier provider, and return a typed proposal.

        The happy path returns an ``InterpretationProposal``. Provider-down and
        rate-limit exhaustion return an ``ErrorResponse`` proposal instead of
        raising, so callers can distinguish provider failure from invalid input.
        """
        self._check_consent_and_retention()
        if not self.settings.model.strip():
            message = "free model name is not configured"
            raise InterpretationProviderError(message)
        if not text.strip() or len(text) > MAX_INPUT_CHARACTERS:
            message = "free model interpretation input is invalid"
            raise InterpretationProviderError(message)

        credential = SecretStr(self.keys.connector_secret(self.settings.key_reference))
        payload: dict[str, JsonValue] = {
            "model": self.settings.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return only JSON matching the supplied LifeOS day-plan schema. "
                        "Preserve canonical metadata and protected activities. "
                        "Do not execute actions or infer approval from conversation text."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "request": text,
                            "base_plan": self.base_plan.model_dump(mode="json"),
                            "schema": TypeAdapter[JsonValue](JsonValue).validate_python(
                                DayPlanRequest.model_json_schema()
                            ),
                        }
                    ),
                },
            ],
            "temperature": 0.0,
        }

        try:
            response_body = self._request_with_retry(payload, credential)
        except RateLimitError:
            return ErrorResponse(
                status="rate_limited",
                error="free model provider rate limited",
            )
        except RetryableProviderError:
            return ErrorResponse(
                status="provider_unavailable",
                error="free model provider retry limit exceeded",
            )
        except InterpretationProviderError as error:
            return ErrorResponse(status="provider_unavailable", error=str(error))

        plan = self._parse_plan(response_body)
        preserve_plan_constraints(self.base_plan, plan)
        return InterpretationProposal(
            proposal_id=RecordId(uuid4()),
            source_event_id=source_event_id,
            provider_version=f"free-model:{self.settings.model}",
            plan_request=plan,
        )
