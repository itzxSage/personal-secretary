from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from secretary_service.conversation_api import TranscriptFinal
from secretary_service.enrollment import DeviceId, DeviceRegistry
from secretary_service.models import ActorId
from secretary_service.relay_api import EventBatch
from secretary_service.relay_auth import RequestAuthenticationError, RequestAuthenticator
from secretary_service.relay_store import RelayAccessError, RelayConflictError
from tests.helpers import FakeClock
from tests.postgres_support import PostgresCase
from tests.test_conversation_relay import (
    CONVERSATION,
    DEVICE,
    context,
    provision,
    signed,
    text_events,
)


def prepare(case: PostgresCase, clock: FakeClock) -> None:
    with case.open() as store, store.execution_transaction() as unit:
        provision(unit, clock)
        _ = unit.conversations.create(
            CONVERSATION, "Synthetic conversation", DEVICE, context(clock)
        )


def test_delivery_retries_and_pagination_survive_reconnect(
    pg_case: PostgresCase, clock: FakeClock
) -> None:
    prepare(pg_case, clock)
    events = text_events(clock)
    with pg_case.open() as store, store.execution_transaction() as unit:
        assert unit.conversations.append(CONVERSATION, DEVICE, events, context(clock)) == 3
        count = store.verify_audit_chain().entries_verified
    with pg_case.open() as store, store.execution_transaction() as unit:
        assert unit.conversations.append(CONVERSATION, DEVICE, events, context(clock)) == 3
        assert store.verify_audit_chain().entries_verified == count
        assert unit.conversations.events(CONVERSATION, DEVICE, after=1, limit=1) == events[1:]
        assert unit.conversations.events(CONVERSATION, DEVICE, after=2) == []


def test_failed_batch_rolls_back_content_and_request_consumption(
    pg_case: PostgresCase, clock: FakeClock
) -> None:
    prepare(pg_case, clock)
    events = text_events(clock)
    invalid = [events[0], events[1].model_copy(update={"sequence": 4})]

    def attempt() -> None:
        with pg_case.open() as store, store.execution_transaction() as unit:
            assert unit.consumption.consume((("request", "failed-batch"),))
            _ = unit.conversations.append(CONVERSATION, DEVICE, invalid, context(clock))

    with pytest.raises(RelayConflictError):
        attempt()
    with pg_case.open() as store, store.execution_transaction() as unit:
        assert unit.conversations.events(CONVERSATION, DEVICE) == []
        assert unit.consumption.consume((("request", "failed-batch"),))
        assert unit.conversations.append(CONVERSATION, DEVICE, events, context(clock)) == 3


def test_signed_phone_request_and_replay_use_durable_device_state(
    pg_case: PostgresCase, clock: FakeClock
) -> None:
    prepare(pg_case, clock)
    events = text_events(clock)
    body = EventBatch(events=events).model_dump_json().encode()
    target = f"/v1/conversations/{CONVERSATION}/events"
    proof = signed(clock, "POST", target, body)
    with pg_case.open() as store, store.execution_transaction() as unit:
        authenticator = RequestAuthenticator(
            DeviceRegistry(clock, unit.devices), unit.consumption, clock
        )
        device = authenticator.verify(proof, "POST", target, body, "test-peer")
        assert unit.conversations.append(CONVERSATION, device, events, context(clock)) == 3
    with pg_case.open() as store, store.execution_transaction() as unit:
        authenticator = RequestAuthenticator(
            DeviceRegistry(clock, unit.devices), unit.consumption, clock
        )
        with pytest.raises(RequestAuthenticationError):
            _ = authenticator.verify(proof, "POST", target, body, "test-peer")


def test_membership_and_revocation_deny_content_access(
    pg_case: PostgresCase, clock: FakeClock
) -> None:
    prepare(pg_case, clock)
    outsider = uuid4()
    with pg_case.open() as store, store.execution_transaction() as unit:
        provision(unit, clock, outsider, uuid4(), "different-peer")
        with pytest.raises(RelayAccessError):
            _ = unit.conversations.events(CONVERSATION, outsider)
        _ = DeviceRegistry(clock, unit.devices).revoke(DeviceId(str(DEVICE)), ActorId("owner"))
    with pg_case.open() as store, store.execution_transaction() as unit:
        with pytest.raises(RelayAccessError):
            _ = unit.conversations.events(CONVERSATION, DEVICE)
        with pytest.raises(RelayAccessError):
            _ = unit.conversations.append(CONVERSATION, DEVICE, text_events(clock), context(clock))


def test_duplicate_id_with_changed_content_is_rejected(
    pg_case: PostgresCase, clock: FakeClock
) -> None:
    prepare(pg_case, clock)
    events = text_events(clock)
    with pg_case.open() as store, store.execution_transaction() as unit:
        _ = unit.conversations.append(CONVERSATION, DEVICE, events, context(clock))
        final = events[-1].payload
        assert isinstance(final, TranscriptFinal)
        altered = events[-1].model_copy(
            update={"payload": final.model_copy(update={"text": "changed"})}
        )
        with pytest.raises(RelayConflictError):
            _ = unit.conversations.append(CONVERSATION, DEVICE, [altered], context(clock))
        assert unit.conversations.events(CONVERSATION, DEVICE) == events


@pytest.mark.parametrize("expired", [False, True])
def test_delete_or_retention_purges_ciphertext_and_blocks_recreation(
    pg_case: PostgresCase, clock: FakeClock, expired: bool
) -> None:
    prepare(pg_case, clock)
    with pg_case.open() as store, store.execution_transaction() as unit:
        _ = unit.conversations.append(CONVERSATION, DEVICE, text_events(clock), context(clock))
        if expired:
            clock.advance(timedelta(days=181))
            assert unit.conversations.expire(clock.now(), context(clock)) == 1
        else:
            assert unit.conversations.delete(CONVERSATION, DEVICE, context(clock)) == 2
        with pytest.raises(RelayAccessError):
            _ = unit.conversations.events(CONVERSATION, DEVICE)
        with pytest.raises(RelayConflictError):
            _ = unit.conversations.create(CONVERSATION, "recreated", DEVICE, context(clock))
    with psycopg.connect(pg_case.dsn) as connection:
        assert connection.execute("SELECT count(*) FROM lifeos_conversation_events").fetchone() == (
            0,
        )
        assert connection.execute("SELECT count(*) FROM lifeos_conversations").fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM lifeos_conversation_deletions"
        ).fetchone() == (1,)


def test_concurrent_exact_retries_do_not_duplicate_events(
    pg_case: PostgresCase, clock: FakeClock
) -> None:
    prepare(pg_case, clock)
    events = text_events(clock)
    barrier = Barrier(2)

    def append(_index: int) -> int:
        with pg_case.open() as store:
            _ = barrier.wait(timeout=10)
            with store.execution_transaction() as unit:
                return unit.conversations.append(CONVERSATION, DEVICE, events, context(clock))

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(append, range(2))) == [3, 3]
    with pg_case.open() as store, store.execution_transaction() as unit:
        assert unit.conversations.events(CONVERSATION, DEVICE) == events
