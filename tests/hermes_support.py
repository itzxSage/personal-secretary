"""Local HTTP fixture implementing the narrow pinned Hermes adapter contract."""

import json
from collections.abc import Generator
from dataclasses import dataclass
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import cast, override

import pytest
from pydantic import JsonValue, TypeAdapter

from secretary_service.hermes.adapter import HermesInterpretationAdapter
from secretary_service.hermes.contracts import HERMES_REVISION, HermesRuntimeReview, HermesSettings
from secretary_service.hermes.transport import HermesHTTPTransport
from tests.test_openai_interpreter import RecordingKeys, make_adapter


class HermesFixtureServer(HTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), Handler)
        self.requests: list[tuple[str, JsonValue]] = []
        self.responses: dict[str, bytes] = {}
        self.status: int = 200


class Handler(BaseHTTPRequestHandler):
    @override
    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        self._respond(None)

    def do_POST(self) -> None:
        data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self._respond(TypeAdapter[JsonValue](JsonValue).validate_json(data))

    def _respond(self, data: JsonValue) -> None:
        server = cast("HermesFixtureServer", self.server)
        assert self.headers.get("Authorization") == "Bearer synthetic-test-token"
        server.requests.append((self.path, data))
        self.send_response(server.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        _ = self.wfile.write(server.responses.get(self.path, b"{}"))


@dataclass
class HermesCase:
    adapter: HermesInterpretationAdapter
    server: HermesFixtureServer
    consent: list[bool]
    review: list[HermesRuntimeReview | None]


@pytest.fixture
def hermes_case() -> Generator[HermesCase]:
    direct, _, _, _ = make_adapter()
    consent = [True]
    review: list[HermesRuntimeReview | None] = [
        HermesRuntimeReview(
            deployment_id="synthetic-hermes",
            revision=HERMES_REVISION,
            evidence_reference="fixture-only-not-production",
            verified_at=direct.clock.now(),
            valid_until=direct.clock.now() + timedelta(hours=1),
        )
    ]
    server = HermesFixtureServer()
    server.responses = {
        "/v1/capabilities": json.dumps(
            {
                "object": "hermes.api_server.capabilities",
                "platform": "hermes-agent",
                "auth": {"type": "bearer", "required": True},
                "features": {"chat_completions": True},
            }
        ).encode(),
        "/v1/toolsets": json.dumps(
            {
                "object": "list",
                "platform": "api_server",
                "data": [{"name": "terminal", "enabled": False, "tools": ["terminal"]}],
            }
        ).encode(),
        "/v1/chat/completions": reply(direct.base_plan.model_dump_json()),
    }
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    adapter = HermesInterpretationAdapter(
        HermesSettings(
            deployment_id="synthetic-hermes",
            model="hermes-agent",
            key_reference="openai-test-reference",
            consent_granted=lambda: consent[0],
            runtime_review=lambda: review[0],
        ),
        direct.clock,
        RecordingKeys(),
        HermesHTTPTransport(f"http://127.0.0.1:{server.server_port}", allow_loopback_http=True),
        direct.base_plan,
    )
    try:
        yield HermesCase(adapter, server, consent, review)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def reply(content: str, *, finish_reason: str = "stop", tool_calls: bool = False) -> bytes:
    return json.dumps(
        {
            "object": "chat.completion",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": content,
                        "tool_calls": [{"name": "execute"}] if tool_calls else [],
                    },
                    "finish_reason": finish_reason,
                }
            ],
        }
    ).encode()
