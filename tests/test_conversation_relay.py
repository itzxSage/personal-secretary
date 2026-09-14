"""Durable relay delivery, access control, atomicity, and request authentication."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from secretary_service.canonical import CanonicalIdentity
from secretary_service.conversation_api import (
    Cancellation,
    ConversationDevice,
    ConversationEvent,
    ConversationEventKind,
    DeviceKind,
    Resume,
    TranscriptFinal,
    TranscriptPartial,
)
from secretary_service.enrollment import DeviceId, DeviceRegistry
from secretary_service.keys import DeterministicTestKeyProvider
from secretary_service.models import ActorId, CorrelationId, RecordId, TransitionContext
from secretary_service.postgres_store import PostgresExecutionUnit
from secretary_service.relay_auth import (
    RequestAuthenticationError,
    RequestAuthenticator,
    RequestProof,
)
from secretary_service.relay_store import RelayAccessError, RelayConflictError
from secretary_service.storage import EncryptedStateStore
from tests.helpers import FakeClock

DEVICE = UUID("33333333-3333-4333-8333-333333333333")
PERSON = UUID("11111111-1111-4111-8111-111111111111")
CONVERSATION = UUID("55555555-5555-4555-8555-555555555555")
SIGNING_KEY = Ed25519PrivateKey.generate()


def context(clock: FakeClock) -> TransitionContext:
    return TransitionContext(
        actor=ActorId(str(DEVICE)),
        correlation_id=CorrelationId(str(uuid4())),
        occurred_at=clock.now(),
    )


def provision(
    store: EncryptedStateStore | PostgresExecutionUnit,
    clock: FakeClock,
    device_id: UUID = DEVICE,
    participant_id: UUID = PERSON,
    fingerprint: str = "test-peer",
) -> None:
    devices = DeviceRegistry(clock, store.devices)
    _ = devices.enroll(
        DeviceId(str(device_id)),
        fingerprint,
        ActorId("local-setup"),
        approval_public_key=SIGNING_KEY.public_key().public_bytes_raw().hex(),
    )
    store.conversations.provision(
        CanonicalIdentity(
            record_id=RecordId(participant_id),
            created_at=clock.now(),
            state="active",
            display_name="Local user",
            is_primary=True,
        ),
        ConversationDevice(
            device_id=device_id,
            participant_id=participant_id,
            kind=DeviceKind.IOS,
            name="Phone",
            created_at=clock.now(),
        ),
        context(clock),
    )


def text_events(clock: FakeClock) -> list[ConversationEvent]:
    turn = uuid4()
    return [
        ConversationEvent(
            event_id=uuid4(),
            conversation_id=CONVERSATION,
            device_id=DEVICE,
            participant_id=PERSON,
            occurred_at=clock.now(),
            sequence=index,
            kind=kind,
            payload=payload,
        )
        for index, kind, payload in (
            (1, ConversationEventKind.TRANSCRIPT_PARTIAL, TranscriptPartial(turn_id=turn, text="")),
            (
                2,
                ConversationEventKind.TRANSCRIPT_FINAL,
                TranscriptFinal(turn_id=turn, text="Private message"),
            ),
        )
    ]


def signed(
    clock: FakeClock, method: str = "POST", target: str = "/v1/conversations", body: bytes = b"{}"
) -> RequestProof:
    proof = RequestProof(DEVICE, uuid4(), int(clock.now().timestamp()), "")
    return replace(
        proof, signature=SIGNING_KEY.sign(proof.signing_bytes(method, target, body)).hex()
    )


def test_python_accepts_cryptokit_signature_vector() -> None:
    # Captured from CryptoKit using the public 0x42 test seed and the shared wire bytes.
    proof = RequestProof(DEVICE, UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"), 1767225600, "")
    signature = bytes.fromhex(
        """a726f6c11ac85f7d06ea1f68896a3fdc83e4c8ce7eee73a4ee95ca9853fd719d
        b03bba43898c81bc0c8faf34a52788c14d2f167bb140039ebe21da2a56fe520f"""
    )
    key = Ed25519PrivateKey.from_private_bytes(bytes([0x42]) * 32).public_key()
    key.verify(signature, proof.signing_bytes("POST", "/v1/conversations", b"{}"))


def test_delivery_and_exact_retry_survive_restart(
    tmp_path: Path, clock: FakeClock, keys: DeterministicTestKeyProvider
) -> None:
    path = tmp_path / "relay.sqlite"
    events = text_events(clock)
    with EncryptedStateStore.open(path, keys, clock) as store:
        provision(store, clock)
        created = store.conversations.create(CONVERSATION, "Private title", DEVICE, context(clock))
        assert (
            store.conversations.create(CONVERSATION, "Private title", DEVICE, context(clock))
            == created
        )
        assert store.conversations.append(CONVERSATION, DEVICE, events, context(clock)) == 3
        audit_count = len(store.audit_entries())
    with EncryptedStateStore.open(path, keys, clock) as store:
        assert store.conversations.events(CONVERSATION, DEVICE) == events
        assert store.conversations.events(CONVERSATION, DEVICE, after=1, limit=1) == events[1:]
        assert store.conversations.append(CONVERSATION, DEVICE, events, context(clock)) == 3
        assert len(store.audit_entries()) == audit_count
        assert len(store.canonical_records().conversation_events) == 2
        assert all("Private" not in entry.model_dump_json() for entry in store.audit_entries())
        assert store.verify_audit_chain().entries_verified == audit_count
    assert b"Private message" not in path.read_bytes()
    assert b"Private title" not in path.read_bytes()


def test_invalid_batch_rolls_back_events_and_audit(
    store: EncryptedStateStore, clock: FakeClock
) -> None:
    provision(store, clock)
    _ = store.conversations.create(CONVERSATION, "Test", DEVICE, context(clock))
    events = text_events(clock)
    before = store.audit_entries()
    with pytest.raises(RelayConflictError):
        _ = store.conversations.append(
            CONVERSATION,
            DEVICE,
            [events[0], events[1].model_copy(update={"sequence": 9})],
            context(clock),
        )
    assert store.conversations.events(CONVERSATION, DEVICE) == []
    assert store.audit_entries() == before
    assert store.conversations.append(CONVERSATION, DEVICE, events, context(clock)) == 3
    with pytest.raises(RelayConflictError):
        _ = store.conversations.append(
            CONVERSATION, DEVICE, [events[0].model_copy(update={"sequence": 8})], context(clock)
        )
    assert store.conversations.events(CONVERSATION, DEVICE) == events


def test_no_foreign_conversation_or_device_spoofing(
    store: EncryptedStateStore, clock: FakeClock
) -> None:
    provision(store, clock)
    foreign = uuid4()
    provision(store, clock, foreign, uuid4(), "foreign-peer")
    _ = store.conversations.create(CONVERSATION, "Test", DEVICE, context(clock))
    with pytest.raises(RelayAccessError):
        _ = store.conversations.events(CONVERSATION, foreign)
    with pytest.raises(RelayAccessError):
        _ = store.conversations.create(CONVERSATION, "Test", foreign, context(clock))
    with pytest.raises(RelayAccessError):
        _ = store.conversations.append(
            CONVERSATION,
            DEVICE,
            [text_events(clock)[0].model_copy(update={"device_id": foreign})],
            context(clock),
        )
    with pytest.raises(RelayConflictError):
        _ = store.conversations.append(
            CONVERSATION,
            DEVICE,
            [text_events(clock)[0].model_copy(update={"participant_id": uuid4()})],
            context(clock),
        )


def test_cancellation_state_survives_restart(
    tmp_path: Path, clock: FakeClock, keys: DeterministicTestKeyProvider
) -> None:
    path = tmp_path / "relay.sqlite"
    partial, final = text_events(clock)
    assert isinstance(partial.payload, TranscriptPartial)
    cancelled = partial.model_copy(
        update={
            "event_id": uuid4(),
            "sequence": 2,
            "kind": ConversationEventKind.CANCELLATION,
            "payload": Cancellation(turn_id=partial.payload.turn_id, reason="user cancelled"),
        }
    )
    with EncryptedStateStore.open(path, keys, clock) as store:
        provision(store, clock)
        _ = store.conversations.create(CONVERSATION, "Test", DEVICE, context(clock))
        _ = store.conversations.append(CONVERSATION, DEVICE, [partial, cancelled], context(clock))
    with EncryptedStateStore.open(path, keys, clock) as store:
        with pytest.raises(RelayConflictError):
            _ = store.conversations.append(
                CONVERSATION, DEVICE, [final.model_copy(update={"sequence": 3})], context(clock)
            )
        resume = partial.model_copy(
            update={
                "event_id": uuid4(),
                "sequence": 3,
                "kind": ConversationEventKind.RESUME,
                "payload": Resume(turn_id=uuid4(), resume_from_turn_id=partial.payload.turn_id),
            }
        )
        assert store.conversations.append(CONVERSATION, DEVICE, [resume], context(clock)) == 4


@pytest.mark.parametrize(
    "change", ["body", "path", "method", "peer", "stale", "future", "revoked", "signature"]
)
def test_request_auth_fails_closed(
    change: str, store: EncryptedStateStore, clock: FakeClock
) -> None:
    provision(store, clock)
    proof = signed(clock)
    devices = DeviceRegistry(clock, store.devices)
    if change == "revoked":
        _ = devices.revoke(DeviceId(str(DEVICE)), ActorId("owner"))
    if change in {"stale", "future"}:
        clock.advance(timedelta(seconds=61 if change == "stale" else -6))
    if change == "signature":
        proof = replace(proof, signature="00" * 64)
    auth = RequestAuthenticator(devices, store.authority_consumption, clock)
    with pytest.raises(RequestAuthenticationError):
        _ = auth.verify(
            proof,
            "GET" if change == "method" else "POST",
            "/v1/other" if change == "path" else "/v1/conversations",
            b"altered" if change == "body" else b"{}",
            "wrong-peer" if change == "peer" else "test-peer",
        )


def test_nonce_replay_rejected_after_restart(
    tmp_path: Path, clock: FakeClock, keys: DeterministicTestKeyProvider
) -> None:
    path = tmp_path / "relay.sqlite"
    proof = signed(clock)
    with EncryptedStateStore.open(path, keys, clock) as store:
        provision(store, clock)
        auth = RequestAuthenticator(
            DeviceRegistry(clock, store.devices), store.authority_consumption, clock
        )
        assert auth.verify(proof, "POST", "/v1/conversations", b"{}", "test-peer") == DEVICE
    with EncryptedStateStore.open(path, keys, clock) as store:
        auth = RequestAuthenticator(
            DeviceRegistry(clock, store.devices), store.authority_consumption, clock
        )
        with pytest.raises(RequestAuthenticationError):
            _ = auth.verify(proof, "POST", "/v1/conversations", b"{}", "test-peer")


def test_user_deletion_purges_canonical_content_and_keeps_audit_proof(
    store: EncryptedStateStore, clock: FakeClock
) -> None:
    provision(store, clock)
    events = text_events(clock)
    _ = store.conversations.create(CONVERSATION, "Delete me", DEVICE, context(clock))
    _ = store.conversations.append(CONVERSATION, DEVICE, events, context(clock))
    assert store.conversations.delete(CONVERSATION, DEVICE, context(clock)) == 2
    snapshot = store.canonical_records()
    assert snapshot.conversations == ()
    assert snapshot.conversation_events == ()
    assert store.verify_audit_chain().entries_verified == len(store.audit_entries())
    assert store.audit_entries()[-1].action_class == "conversation.deleted"
    with pytest.raises(RelayAccessError):
        _ = store.conversations.events(CONVERSATION, DEVICE)


def test_fixed_retention_expiry_survives_restart(
    tmp_path: Path, clock: FakeClock, keys: DeterministicTestKeyProvider
) -> None:
    path = tmp_path / "retention.sqlite"
    with EncryptedStateStore.open(path, keys, clock) as store:
        provision(store, clock)
        _ = store.conversations.create(CONVERSATION, "Expires", DEVICE, context(clock))
        assert store.conversations.expire(clock.now(), context(clock)) == 0
    clock.advance(timedelta(days=180))
    with EncryptedStateStore.open(path, keys, clock) as store:
        assert store.conversations.expire(clock.now(), context(clock)) == 1
        assert store.canonical_records().conversations == ()
        assert store.conversations.expire(clock.now(), context(clock)) == 0
