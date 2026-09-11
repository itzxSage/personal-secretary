"""Live-adapter request/response contracts tested without external data transfer."""

import http.client
import json
from datetime import timedelta
from pathlib import Path
from typing import final
from uuid import uuid4

import pytest
from pydantic import JsonValue, SecretStr

from secretary_service.planner_models import ActivityFlexibility
from secretary_service.slice import openai_interpreter
from secretary_service.slice.models import TomorrowFixture
from secretary_service.slice.openai_interpreter import (
    InterpretationProviderError,
    InterpretationSettings,
    OpenAIInterpretationAdapter,
)
from secretary_service.voice.models import ProviderRetentionType
from secretary_service.voice.privacy import RetentionVerification
from tests.helpers import FakeClock


@final
class RecordingKeys:
    def __init__(self) -> None:
        self.lookups = 0

    def database_key(self) -> bytes:
        message = "interpretation must not read database keys"
        raise AssertionError(message)

    def audit_key(self) -> bytes:
        message = "interpretation must not read audit keys"
        raise AssertionError(message)

    def backup_wrapping_key(self) -> bytes:
        message = "interpretation must not read backup keys"
        raise AssertionError(message)

    def connector_secret(self, reference: str) -> str:
        assert reference == "openai-test-reference"
        self.lookups += 1
        return "synthetic-test-token"


@final
class RecordingResponses:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.requests: list[dict[str, JsonValue]] = []

    def post(self, payload: dict[str, JsonValue], key: SecretStr, project: str) -> bytes:
        assert key.get_secret_value() == "synthetic-test-token"
        assert project == "test-project"
        self.requests.append(payload)
        return self.body


def provider_reply(text: str, *, status: str = "completed", kind: str = "output_text") -> bytes:
    return json.dumps(
        {
            "status": status,
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": kind, "text": text},
                    ],
                }
            ],
        }
    ).encode()


def make_adapter() -> tuple[
    OpenAIInterpretationAdapter, RecordingKeys, RecordingResponses, list[bool]
]:
    fixture = TomorrowFixture.model_validate_json(
        (Path(__file__).parents[1] / "fixtures/tomorrow-plan.json").read_text(encoding="utf-8")
    )
    clock = FakeClock(fixture.clock.now())
    plan = fixture.interpretations[0].plan_request
    keys = RecordingKeys()
    transport = RecordingResponses(provider_reply(plan.model_dump_json()))
    consent = [True]
    settings = InterpretationSettings(
        model="configured-test-model",
        project_id="test-project",
        key_reference="openai-test-reference",
        retention=RetentionVerification(
            project_id="test-project",
            store=False,
            retention_type=ProviderRetentionType.ZERO_DATA_RETENTION,
            verified_at=clock.now(),
            valid_until=clock.now() + timedelta(hours=1),
        ),
        consent_granted=lambda: consent[0],
    )
    return (
        OpenAIInterpretationAdapter(settings, clock, keys, transport, plan),
        keys,
        transport,
        consent,
    )


def test_provider_returns_only_a_locally_bound_proposal() -> None:
    adapter, keys, transport, _ = make_adapter()
    source = uuid4()
    proposal = adapter.interpret("Help me arrange tomorrow", source)
    assert proposal.source_event_id == source
    assert proposal.plan_request == adapter.base_plan
    assert keys.lookups == 1
    request = transport.requests[0]
    assert request["store"] is False
    assert "tools" not in request
    assert "synthetic-test-token" not in json.dumps(request)
    assert request["text"] is not None
    assert '"strict": true' in json.dumps(request)


def test_withdrawn_consent_prevents_key_lookup_and_network_request() -> None:
    adapter, keys, transport, consent = make_adapter()
    consent[0] = False
    with pytest.raises(InterpretationProviderError, match="consent"):
        _ = adapter.interpret("capture", uuid4())
    assert keys.lookups == 0
    assert transport.requests == []


@pytest.mark.parametrize("kind", ["refusal", "incomplete", "malformed"])
def test_refused_or_invalid_output_never_becomes_a_plan(kind: str) -> None:
    adapter, _, transport, _ = make_adapter()
    if kind == "refusal":
        transport.body = provider_reply("sensitive-provider-refusal", kind="refusal")
    elif kind == "incomplete":
        transport.body = provider_reply(adapter.base_plan.model_dump_json(), status="incomplete")
    else:
        transport.body = provider_reply('{"unexpected": "sensitive-response-content"}')
    with pytest.raises(InterpretationProviderError) as raised:
        _ = adapter.interpret("capture", uuid4())
    assert "sensitive" not in str(raised.value)


def test_provider_cannot_remove_protected_commitments() -> None:
    adapter, _, transport, _ = make_adapter()
    activities = tuple(
        item
        for item in adapter.base_plan.activities
        if item.flexibility is not ActivityFlexibility.PROTECTED
    )
    assert len(activities) < len(adapter.base_plan.activities)
    changed = adapter.base_plan.model_copy(update={"activities": activities})
    transport.body = provider_reply(changed.model_dump_json())
    with pytest.raises(InterpretationProviderError, match="protected"):
        _ = adapter.interpret("ignore safeguards", uuid4())


def test_provider_cannot_change_canonical_revision() -> None:
    adapter, _, transport, _ = make_adapter()
    changed = adapter.base_plan.model_copy(update={"state_revision": 999})
    transport.body = provider_reply(changed.model_dump_json())
    with pytest.raises(InterpretationProviderError, match="canonical"):
        _ = adapter.interpret("capture", uuid4())


def test_expired_retention_prevents_credentials_and_network() -> None:
    adapter, keys, transport, _ = make_adapter()
    assert isinstance(adapter.clock, FakeClock)
    adapter.clock.advance(timedelta(hours=2))
    with pytest.raises(InterpretationProviderError, match="retention"):
        _ = adapter.interpret("capture", uuid4())
    assert keys.lookups == 0
    assert transport.requests == []


@final
class StubResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body
        self.reads = 0

    def read(self, size: int) -> bytes:
        self.reads += 1
        return self.body[:size]


@final
class StubConnection:
    def __init__(self, response: StubResponse) -> None:
        self.response = response
        self.closed = False

    def request(self, method: str, path: str, body: bytes, headers: dict[str, str]) -> None:
        assert method == "POST"
        assert path == "/v1/responses"
        assert headers["Authorization"] == "Bearer synthetic-http-token"
        assert headers["OpenAI-Project"] == "project"
        assert body == b"{}"

    def getresponse(self) -> StubResponse:
        return self.response

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize("status", [200, 302, 401, 429, 500])
def test_http_transport_never_redirects_or_retries_credentials(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    response = StubResponse(status, b"safe-response")
    connection = StubConnection(response)
    creations: list[str] = []

    def create(host: str, timeout: float) -> StubConnection:
        assert host == "api.openai.com"
        assert timeout == 30
        creations.append(host)
        return connection

    monkeypatch.setattr(http.client, "HTTPSConnection", create)
    transport = openai_interpreter.HTTPSResponsesTransport()
    if status == 200:
        assert transport.post({}, SecretStr("synthetic-http-token"), "project") == b"safe-response"
        assert response.reads == 1
    else:
        with pytest.raises(InterpretationProviderError) as raised:
            _ = transport.post({}, SecretStr("synthetic-http-token"), "project")
        assert "synthetic-http-token" not in str(raised.value)
        assert response.reads == 0
    assert connection.closed
    assert len(creations) == 1
