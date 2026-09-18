"""Relay create() is idempotent on title match but rejects a title mismatch with HTTP 409.

sendAgentTurn() create()s the conversation as "LifeOS" while synchronize() already
create()d it as "Secretary". The client must treat that 409 as "already exists" and
still run the turn — otherwise push-to-talk surfaces "couldn't get a reply".
"""

import json
from pathlib import Path

from secretary_service.conversation_turn import (
    AgentUtterance,
    ConversationTurnReply,
    ConversationTurnService,
)
from secretary_service.keys import DeterministicTestKeyProvider
from secretary_service.relay_api import create_relay_app
from secretary_service.storage import EncryptedStateStore
from tests.helpers import FakeClock
from tests.test_conversation_relay import CONVERSATION, provision
from tests.test_relay_https import certificates, serving


class _EchoAgent:
    """Minimal ConversationAgent stand-in; returns a fixed turn reply."""

    def respond(self, history: tuple[AgentUtterance, ...]) -> ConversationTurnReply:
        del history
        return ConversationTurnReply(reply_text="Echo back.", proposed_actions=())


def _make_factory(store: EncryptedStateStore) -> ConversationTurnService:
    return ConversationTurnService(_EchoAgent(), store.conversations)


def test_create_title_mismatch_409_then_turn_succeeds(
    tmp_path: Path, keys: DeterministicTestKeyProvider, clock: FakeClock
) -> None:
    tls = certificates(tmp_path, clock)
    path = tmp_path / "conflict.sqlite"
    with EncryptedStateStore.open(path, keys, clock) as store:
        provision(store, clock, fingerprint=tls.fingerprint)
    app = create_relay_app(
        lambda: EncryptedStateStore.open(path, keys, clock),
        clock,
        conversation_turn_factory=_make_factory,
    )
    with serving(app, tls, clock, path, keys) as running:
        first = json.dumps({"conversation_id": str(CONVERSATION), "title": "Secretary"}).encode()
        assert running.request("POST", "/v1/conversations", first)[0] == 200

        second = json.dumps({"conversation_id": str(CONVERSATION), "title": "LifeOS"}).encode()
        status, _ = running.request("POST", "/v1/conversations", second)
        assert status == 409  # existing conversation, different title

        # The conversation still exists and a turn must still succeed.
        turn = json.dumps({"conversation_id": str(CONVERSATION), "text": "hi"}).encode()
        assert running.request("POST", "/v1/conversation/turn", turn)[0] == 200
