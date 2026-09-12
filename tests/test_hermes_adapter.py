import json
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import pytest
from pydantic import SecretStr

from secretary_service.hermes.contracts import HERMES_REVISION, HermesEndpoint
from secretary_service.hermes.transport import HermesHTTPTransport
from secretary_service.slice.validation import InterpretationProviderError
from tests.hermes_support import HermesCase, reply

if TYPE_CHECKING:
    from secretary_service.slice.interpreter import InterpretationAdapter


def test_real_http_adapter_returns_bound_proposal(hermes_case: HermesCase) -> None:
    adapter: InterpretationAdapter = hermes_case.adapter
    source_id = uuid4()
    proposal = adapter.interpret("Plan tomorrow", source_id)
    assert proposal.source_event_id == source_id
    assert proposal.plan_request == hermes_case.adapter.base_plan
    assert HERMES_REVISION in proposal.provider_version
    assert [path for path, _ in hermes_case.server.requests] == [
        "/v1/capabilities",
        "/v1/toolsets",
        "/v1/chat/completions",
    ]


@pytest.mark.parametrize("failure", ["consent", "missing", "revision", "expiry", "deployment"])
def test_unreviewed_or_changed_runtime_never_contacted(
    hermes_case: HermesCase, failure: str
) -> None:
    current = hermes_case.review[0]
    assert current is not None
    if failure == "consent":
        hermes_case.consent[0] = False
    elif failure == "missing":
        hermes_case.review[0] = None
    elif failure == "revision":
        hermes_case.review[0] = current.model_copy(update={"revision": "f" * 40})
    elif failure == "expiry":
        hermes_case.review[0] = current.model_copy(update={"valid_until": current.verified_at})
    else:
        hermes_case.review[0] = current.model_copy(update={"deployment_id": "other-tenant"})
    with pytest.raises(InterpretationProviderError):
        _ = hermes_case.adapter.interpret("private input", uuid4())
    assert hermes_case.server.requests == []


def test_enabled_tools_block_post(hermes_case: HermesCase) -> None:
    hermes_case.server.responses["/v1/toolsets"] = json.dumps(
        {
            "object": "list",
            "platform": "api_server",
            "data": [{"name": "terminal", "enabled": True, "tools": ["terminal"]}],
        }
    ).encode()
    with pytest.raises(InterpretationProviderError, match="enabled tools"):
        _ = hermes_case.adapter.interpret("send an email", uuid4())
    assert len(hermes_case.server.requests) == 2


@pytest.mark.parametrize("mode", ["partial", "tool", "malformed", "metadata", "recovery"])
def test_bad_output_cannot_become_plan(hermes_case: HermesCase, mode: str) -> None:
    plan = hermes_case.adapter.base_plan
    body = reply(plan.model_dump_json())
    if mode == "partial":
        body = reply(plan.model_dump_json(), finish_reason="length")
    elif mode == "tool":
        body = reply(plan.model_dump_json(), tool_calls=True)
    elif mode == "malformed":
        body = b'{"object":"unexpected-version"}'
    elif mode == "metadata":
        body = reply(plan.model_copy(update={"state_revision": 999}).model_dump_json())
    elif mode == "recovery":
        activity = next(a for a in plan.activities if not a.recover_missed_deadline)
        changed = activity.model_copy(
            update={"recover_missed_deadline": True, "deadline": plan.window_end}
        )
        body = reply(
            plan.model_copy(
                update={
                    "activities": tuple(
                        changed if a.activity_id == activity.activity_id else a
                        for a in plan.activities
                    )
                }
            ).model_dump_json()
        )
    hermes_case.server.responses["/v1/chat/completions"] = body
    with pytest.raises(InterpretationProviderError):
        _ = hermes_case.adapter.interpret("plan", uuid4())


@pytest.mark.parametrize("status", [301, 401, 429, 500])
def test_failures_are_redacted_without_retry(hermes_case: HermesCase, status: int) -> None:
    hermes_case.server.status = status
    hermes_case.server.responses["/v1/capabilities"] = b"secret-token-private-body"
    with pytest.raises(InterpretationProviderError) as caught:
        _ = hermes_case.adapter.interpret("private input", uuid4())
    assert "secret-token" not in str(caught.value)
    assert "private input" not in str(caught.value)
    assert len(hermes_case.server.requests) == 1


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.com",
        "https://user:password@example.com",
        "https://example.com/admin",
        "https://example.com?key=secret",
        "file:///tmp/agent",
    ],
)
def test_transport_rejects_unsafe_origins(origin: str) -> None:
    with pytest.raises(ValueError, match="invalid Hermes"):
        _ = HermesHTTPTransport(origin)


def test_transport_rejects_admin_path(hermes_case: HermesCase) -> None:
    with pytest.raises(InterpretationProviderError, match="allowlisted"):
        _ = hermes_case.adapter.transport.request(
            cast("HermesEndpoint", cast("object", "/api/admin")),
            None,
            SecretStr("synthetic-test-token"),
            uuid4(),
        )
    assert hermes_case.server.requests == []
