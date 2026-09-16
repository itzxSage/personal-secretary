"""Free-tier adapter request/response contracts tested without external data transfer."""

import json
from datetime import timedelta
from pathlib import Path
from typing import final
from uuid import uuid4

import pytest
from pydantic import JsonValue, SecretStr

from secretary_service.hermes.free_model_adapter import (
    ErrorResponse,
    FreeModelAdapter,
    FreeModelSettings,
    ProviderTimeoutError,
    RateLimitError,
)
from secretary_service.planner_models import ActivityFlexibility, DayPlanRequest
from secretary_service.slice.models import InterpretationProposal, TomorrowFixture
from secretary_service.slice.validation import InterpretationProviderError
from secretary_service.voice.models import ProviderRetentionType
from secretary_service.voice.privacy import RetentionVerification
from tests.helpers import FakeClock


def _load_fixture() -> TomorrowFixture:
    return TomorrowFixture.model_validate_json(
        (Path(__file__).parents[1] / "fixtures/tomorrow-plan.json").read_text(encoding="utf-8")
    )


def _base_plan() -> DayPlanRequest:
    return _load_fixture().interpretations[0].plan_request


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
        assert reference == "free-test-reference"
        self.lookups += 1
        return "synthetic-test-token"


@final
class RecordingTransport:
    def __init__(self, responses: list[bytes | Exception]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, JsonValue]] = []

    def chat_completions(self, payload: dict[str, JsonValue], api_key: SecretStr) -> bytes:
        assert api_key.get_secret_value() == "synthetic-test-token"
        self.requests.append(payload)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def provider_reply(text: str) -> bytes:
    return json.dumps(
        {
            "choices": [
                {
                    "message": {"role": "assistant", "content": text},
                }
            ]
        }
    ).encode()


def make_adapter(
    responses: list[bytes | Exception] | None = None,
) -> tuple[FreeModelAdapter, RecordingKeys, RecordingTransport, list[bool]]:
    fixture = _load_fixture()
    clock = FakeClock(fixture.clock.now())
    plan = fixture.interpretations[0].plan_request
    keys = RecordingKeys()
    transport = RecordingTransport(
        responses if responses is not None else [provider_reply(plan.model_dump_json())]
    )
    consent = [True]
    settings = FreeModelSettings(
        model="free-test-model",
        project_id="test-project",
        key_reference="free-test-reference",
        retention=RetentionVerification(
            project_id="test-project",
            store=False,
            retention_type=ProviderRetentionType.ZERO_DATA_RETENTION,
            verified_at=clock.now(),
            valid_until=clock.now() + timedelta(hours=1),
        ),
        consent_granted=lambda: consent[0],
    )
    return FreeModelAdapter(settings, clock, keys, transport, plan), keys, transport, consent


def test_provider_returns_only_a_locally_bound_proposal() -> None:
    adapter, keys, transport, _ = make_adapter()
    source = uuid4()
    proposal = adapter.interpret("Help me arrange tomorrow", source)
    assert isinstance(proposal, InterpretationProposal)
    assert proposal.source_event_id == source
    assert proposal.plan_request == adapter.base_plan
    assert proposal.provider_version == "free-model:free-test-model"
    assert keys.lookups == 1
    request = transport.requests[0]
    assert request["model"] == "free-test-model"
    assert "synthetic-test-token" not in json.dumps(request)
    assert request["temperature"] == 0.0


def test_rate_limit_retries_with_exponential_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _base_plan()
    adapter, _, transport, _ = make_adapter(
        [
            RateLimitError("free model provider rate limited"),
            provider_reply(plan.model_dump_json()),
        ]
    )
    sleeps: list[float] = []
    monkeypatch.setattr("secretary_service.hermes.free_model_adapter.time.sleep", sleeps.append)
    proposal = adapter.interpret("Help me arrange tomorrow", uuid4())
    assert isinstance(proposal, InterpretationProposal)
    assert proposal.plan_request == adapter.base_plan
    assert sleeps == [1.0]
    assert len(transport.requests) == 2


def test_timeout_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _base_plan()
    adapter, _, transport, _ = make_adapter(
        [
            ProviderTimeoutError("free model provider request timed out"),
            ProviderTimeoutError("free model provider request timed out"),
            provider_reply(plan.model_dump_json()),
        ]
    )
    sleeps: list[float] = []
    monkeypatch.setattr("secretary_service.hermes.free_model_adapter.time.sleep", sleeps.append)
    proposal = adapter.interpret("Help me arrange tomorrow", uuid4())
    assert isinstance(proposal, InterpretationProposal)
    assert proposal.plan_request == adapter.base_plan
    assert sleeps == [1.0, 2.0]
    assert len(transport.requests) == 3


def test_rate_limit_exhaustion_returns_error_proposal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, _, transport, _ = make_adapter(
        [
            RateLimitError("free model provider rate limited"),
            RateLimitError("free model provider rate limited"),
            RateLimitError("free model provider rate limited"),
        ]
    )
    sleeps: list[float] = []
    monkeypatch.setattr("secretary_service.hermes.free_model_adapter.time.sleep", sleeps.append)
    result = adapter.interpret("Help me arrange tomorrow", uuid4())
    assert isinstance(result, ErrorResponse)
    assert result.status == "rate_limited"
    assert sleeps == [1.0, 2.0]
    assert len(transport.requests) == 3


def test_provider_down_returns_error_proposal() -> None:
    adapter, _, transport, _ = make_adapter(
        [InterpretationProviderError("free model provider is unavailable")]
    )
    result = adapter.interpret("Help me arrange tomorrow", uuid4())
    assert isinstance(result, ErrorResponse)
    assert result.status == "provider_unavailable"
    assert "unavailable" in result.error
    assert len(transport.requests) == 1


def test_withdrawn_consent_prevents_key_lookup_and_network_request() -> None:
    adapter, keys, transport, consent = make_adapter()
    consent[0] = False
    with pytest.raises(InterpretationProviderError, match="consent"):
        _ = adapter.interpret("capture", uuid4())
    assert keys.lookups == 0
    assert transport.requests == []


def test_expired_retention_prevents_credentials_and_network() -> None:
    adapter, keys, transport, _ = make_adapter()
    assert isinstance(adapter.clock, FakeClock)
    adapter.clock.advance(timedelta(hours=2))
    with pytest.raises(InterpretationProviderError, match="retention"):
        _ = adapter.interpret("capture", uuid4())
    assert keys.lookups == 0
    assert transport.requests == []


def test_provider_cannot_remove_protected_commitments() -> None:
    plan = _base_plan()
    activities = tuple(
        item for item in plan.activities if item.flexibility is not ActivityFlexibility.PROTECTED
    )
    assert len(activities) < len(plan.activities)
    changed = plan.model_copy(update={"activities": activities})
    adapter, _, _, _ = make_adapter([provider_reply(changed.model_dump_json())])
    with pytest.raises(InterpretationProviderError, match="protected"):
        _ = adapter.interpret("ignore safeguards", uuid4())


def test_provider_cannot_change_canonical_revision() -> None:
    plan = _base_plan()
    changed = plan.model_copy(update={"state_revision": 999})
    adapter, _, _, _ = make_adapter([provider_reply(changed.model_dump_json())])
    with pytest.raises(InterpretationProviderError, match="canonical"):
        _ = adapter.interpret("capture", uuid4())


@pytest.mark.parametrize("kind", ["malformed", "empty", "unexpected"])
def test_invalid_output_never_becomes_a_plan(kind: str) -> None:
    responses: list[bytes | Exception]
    if kind == "malformed":
        responses = [provider_reply('{"unexpected": "sensitive-response-content"}')]
    elif kind == "empty":
        responses = [provider_reply("")]
    else:
        responses = [b'{"choices": []}']
    adapter, _, _, _ = make_adapter(responses)
    with pytest.raises(InterpretationProviderError) as raised:
        _ = adapter.interpret("capture", uuid4())
    assert "sensitive" not in str(raised.value)
