"""Exercise the real TLS handshake, certificate binding, and encrypted HTTP relay."""

import json
import socket
import ssl
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from http.client import HTTPSConnection
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

import pytest
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from fastapi import FastAPI
from fastapi.testclient import TestClient

from secretary_service.authority import Approval, ApprovalProof, ProposalRecord, ProposalState
from secretary_service.enrollment import DeviceId, DeviceRegistry
from secretary_service.google_calendar_sandbox import GoogleCalendarSandbox
from secretary_service.keys import DeterministicTestKeyProvider
from secretary_service.life_knowledge import RoutineFlexibility
from secretary_service.memory import MemoryRecord
from secretary_service.models import ActorId, CorrelationId, Proposal, RecordId, RecordKind
from secretary_service.planner_results import PlanStatus
from secretary_service.relay_api import (
    MAX_BODY_BYTES,
    EventAcknowledgment,
    EventBatch,
    create_relay_app,
)
from secretary_service.relay_tls import PeerBoundH11Protocol, certificate_fingerprint
from secretary_service.storage import EncryptedStateStore
from secretary_service.week_planning import (
    WeekPlanApproval,
    WeekPlanExecutionResult,
    WeekPlanningService,
    WeekPlanProposal,
)
from tests.helpers import FakeClock
from tests.test_conversation_relay import (
    CONVERSATION,
    DEVICE,
    PERSON,
    SIGNING_KEY,
    provision,
    signed,
    text_events,
)
from tests.test_google_calendar import make_adapter
from tests.test_week_planning import routine, seed


# FastAPI serializes computed fields (e.g. PlanBlock.duration_minutes) into the
# wire payload, which the canonical model forbids on input. Clients must tolerate
# unknown keys (like the Swift decoder does), so mirror that behavior here.
def parse_wire_week_plan_proposal(body: bytes) -> WeekPlanProposal:
    """Parse a wire proposal the way a client must: tolerant of computed fields."""
    payload = json.loads(body)
    for block in payload.get("blocks", ()):
        block.pop("duration_minutes", None)
    return WeekPlanProposal.model_validate(payload)


@dataclass(frozen=True)
class TLSMaterial:
    ca: Path
    server_cert: Path
    server_key: Path
    client_cert: Path
    client_key: Path
    fingerprint: str


def certificates(directory: Path, clock: FakeClock) -> TLSMaterial:
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Ephemeral relay test CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(clock.now() - timedelta(days=1))
        .not_valid_after(clock.now() + timedelta(days=730))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_path = directory / "ca.pem"
    _ = ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    fingerprints: list[str] = []
    for name, usage in (
        ("server", ExtendedKeyUsageOID.SERVER_AUTH),
        ("client", ExtendedKeyUsageOID.CLIENT_AUTH),
        ("unbound", ExtendedKeyUsageOID.CLIENT_AUTH),
    ):
        key = ec.generate_private_key(ec.SECP256R1())
        certificate = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
            .issuer_name(ca_name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(clock.now() - timedelta(days=1))
            .not_valid_after(clock.now() + timedelta(days=730))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )
        _ = (directory / f"{name}.pem").write_bytes(
            certificate.public_bytes(serialization.Encoding.PEM)
        )
        _ = (directory / f"{name}-key.pem").write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        fingerprints.append(
            certificate_fingerprint(certificate.public_bytes(serialization.Encoding.DER))
        )
    return TLSMaterial(
        ca_path,
        directory / "server.pem",
        directory / "server-key.pem",
        directory / "client.pem",
        directory / "client-key.pem",
        fingerprints[1],
    )


@dataclass(frozen=True)
class RunningRelay:
    port: int
    tls: TLSMaterial
    clock: FakeClock
    path: Path
    keys: DeterministicTestKeyProvider
    sandbox: object | None = None

    def request(
        self,
        method: str,
        target: str,
        body: bytes = b"",
        *,
        client_cert: Literal["enrolled", "unbound", "none"] = "enrolled",
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes]:
        context = ssl.create_default_context(cafile=str(self.tls.ca))
        if client_cert != "none":
            if client_cert == "unbound":
                context.load_cert_chain(
                    self.tls.ca.parent / "unbound.pem", self.tls.ca.parent / "unbound-key.pem"
                )
            else:
                context.load_cert_chain(self.tls.client_cert, self.tls.client_key)
        connection = HTTPSConnection("localhost", self.port, context=context, timeout=5)
        proof = signed(self.clock, method, target, body)
        request_headers = headers or {
            "Content-Type": "application/json",
            "X-LifeOS-Contract-Version": "1.0.0",
            "X-LifeOS-Device": str(proof.device_id),
            "X-LifeOS-Request-ID": str(proof.request_id),
            "X-LifeOS-Issued-At": str(proof.issued_at),
            "X-LifeOS-Signature": proof.signature,
        }
        try:
            connection.request(method, target, body=body, headers=request_headers)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()


@contextmanager
def serving(
    app: FastAPI,
    tls: TLSMaterial,
    clock: FakeClock,
    path: Path,
    keys: DeterministicTestKeyProvider,
    sandbox: object | None = None,
) -> Generator[RunningRelay]:
    """Run one uvicorn mTLS server and expose the signed request helper to the test."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = cast("tuple[str, int]", listener.getsockname())[1]
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            http=PeerBoundH11Protocol,
            ws="none",
            ssl_certfile=str(tls.server_cert),
            ssl_keyfile=str(tls.server_key),
            ssl_ca_certs=str(tls.ca),
            ssl_cert_reqs=ssl.CERT_REQUIRED,
            access_log=False,
            log_level="error",
            proxy_headers=False,
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                _ = threading.Event().wait(0.01)
            assert server.started
            yield RunningRelay(port, tls, clock, path, keys, sandbox)
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            assert not thread.is_alive()


@pytest.fixture
def relay(
    tmp_path: Path, keys: DeterministicTestKeyProvider, clock: FakeClock
) -> Generator[RunningRelay]:
    tls = certificates(tmp_path, clock)
    path = tmp_path / "relay.sqlite"
    with EncryptedStateStore.open(path, keys, clock) as store:
        provision(store, clock, fingerprint=tls.fingerprint)
    app = create_relay_app(lambda: EncryptedStateStore.open(path, keys, clock), clock)
    with serving(app, tls, clock, path, keys) as running:
        yield running


def person_routine(clock: FakeClock, number: int, title: str) -> MemoryRecord:
    """One PREFERRED routine owned by the enrolled participant, not the ''user'' fixture."""
    record = routine(clock, number, title, RoutineFlexibility.PREFERRED)
    assert record.knowledge is not None
    details = record.knowledge.model_copy(update={"subject_id": str(PERSON)})
    return record.model_copy(update={"knowledge": details})


@pytest.fixture
def planning_relay(
    tmp_path: Path, keys: DeterministicTestKeyProvider, clock: FakeClock
) -> Generator[RunningRelay]:
    """Relay with a governed week-planning service wired to a sandbox calendar provider."""
    tls = certificates(tmp_path, clock)
    path = tmp_path / "planning.sqlite"
    adapter, sandbox = make_adapter(clock)
    with EncryptedStateStore.open(path, keys, clock) as store:
        provision(store, clock, fingerprint=tls.fingerprint)
        seed(store, clock, person_routine(clock, 1, "Deep work"))

    def factory(store: EncryptedStateStore) -> WeekPlanningService:
        devices = DeviceRegistry(clock, store.devices)
        return WeekPlanningService(
            clock,
            devices,
            store,
            adapter,
            b"week-planning-lease-key",
            timedelta(minutes=10),
        )

    app = create_relay_app(
        lambda: EncryptedStateStore.open(path, keys, clock),
        clock,
        week_planning_factory=factory,
        planning_timezone="UTC",
    )
    with serving(app, tls, clock, path, keys, sandbox) as running:
        yield running


def approval_for(
    clock: FakeClock,
    store: EncryptedStateStore,
    proposal_id: RecordId,
    device_id: DeviceId = DeviceId(str(DEVICE)),
) -> Approval:
    """Device-signed approval matching the persisted proposal payload."""
    record = store.read(RecordKind.PROPOSAL, proposal_id)
    assert isinstance(record, Proposal)
    unsigned = Approval(
        fact_id=RecordId(uuid4()),
        proposal_id=record.record_id,
        payload_hash=ProposalRecord(
            proposal_id=record.record_id,
            action_class=record.proposal_type,
            payload=record.payload,
            state=ProposalState(record.state),
            created_at=record.created_at,
        ).payload_hash(),
        issued_at=clock.now(),
        expires_at=clock.now() + timedelta(minutes=5),
        idempotency_key="week-plan-approve",
        actor=ActorId("user"),
        correlation_id=CorrelationId("week-planning"),
        proof=ApprovalProof.DEVICE_SIGNED,
        device_id=device_id,
    )
    return unsigned.model_copy(
        update={"signature": SIGNING_KEY.sign(unsigned.signing_bytes("calendar.apply")).hex()}
    )


def test_real_mtls_delivery_retry_and_resume(relay: RunningRelay) -> None:
    body = json.dumps({"conversation_id": str(CONVERSATION), "title": "HTTP conversation"}).encode()
    status, _ = relay.request("POST", "/v1/conversations", body)
    assert status == 200
    events = text_events(relay.clock)
    batch = EventBatch(events=events).model_dump_json().encode()
    target = f"/v1/conversations/{CONVERSATION}/events"
    status, response = relay.request("POST", target, batch)
    assert status == 200
    acknowledgment = EventAcknowledgment.model_validate_json(response)
    assert acknowledgment.model_dump(mode="json") == {
        "acknowledged_event_ids": [str(event.event_id) for event in events],
        "next_sequence": 3,
    }
    assert relay.request("POST", target, batch) == (status, response)
    status, response = relay.request("GET", target + "?after=1&limit=1")
    assert status == 200
    assert b'"cursor":2' in response
    assert b"Private message" in response


def test_no_client_certificate_rejected_by_tls(relay: RunningRelay) -> None:
    with pytest.raises((ssl.SSLError, OSError)):
        _ = relay.request("POST", "/v1/conversations", b"{}", client_cert="none")


def test_trusted_but_wrong_certificate_cannot_claim_another_device(relay: RunningRelay) -> None:
    assert relay.request("POST", "/v1/conversations", b"{}", client_cert="unbound")[0] == 401


def test_revocation_is_seen_by_running_https_server(relay: RunningRelay) -> None:
    with EncryptedStateStore.open(relay.path, relay.keys, relay.clock) as store:
        _ = DeviceRegistry(relay.clock, store.devices).revoke(
            DeviceId(str(DEVICE)), ActorId("owner")
        )
    assert relay.request("POST", "/v1/conversations", b"{}")[0] == 401


def test_invalid_signature_and_large_body_are_rejected(relay: RunningRelay) -> None:
    proof = signed(relay.clock)
    status, body = relay.request(
        "POST",
        "/v1/conversations",
        b"sensitive invalid input",
        headers={
            "X-LifeOS-Contract-Version": "1.0.0",
            "X-LifeOS-Device": str(proof.device_id),
            "X-LifeOS-Request-ID": str(proof.request_id),
            "X-LifeOS-Issued-At": str(proof.issued_at),
            "X-LifeOS-Signature": proof.signature,
        },
    )
    assert status == 401
    assert b"sensitive" not in body
    assert relay.request("POST", "/v1/conversations", b"x" * (MAX_BODY_BYTES + 1))[0] == 413


def test_parameter_validation_does_not_echo_submitted_content(relay: RunningRelay) -> None:
    status, body = relay.request(
        "GET", f"/v1/conversations/{CONVERSATION}/events?after=private-message"
    )
    assert status == 422
    assert b"private-message" not in body


def test_signed_delete_purges_conversation_and_is_not_replayable(relay: RunningRelay) -> None:
    create = json.dumps(
        {"conversation_id": str(CONVERSATION), "title": "Delete over HTTPS"}
    ).encode()
    assert relay.request("POST", "/v1/conversations", create)[0] == 200
    target = f"/v1/conversations/{CONVERSATION}"
    proof = signed(relay.clock, "DELETE", target, b"")
    headers = {
        "X-LifeOS-Contract-Version": "1.0.0",
        "X-LifeOS-Device": str(proof.device_id),
        "X-LifeOS-Request-ID": str(proof.request_id),
        "X-LifeOS-Issued-At": str(proof.issued_at),
        "X-LifeOS-Signature": proof.signature,
    }
    assert relay.request("DELETE", target, headers=headers) == (200, b'{"deleted":true}')
    assert relay.request("DELETE", target, headers=headers)[0] == 401


def test_proxy_headers_cannot_supply_peer_identity(
    tmp_path: Path, keys: DeterministicTestKeyProvider, clock: FakeClock
) -> None:
    path = tmp_path / "relay.sqlite"
    app = create_relay_app(lambda: EncryptedStateStore.open(path, keys, clock), clock)
    with TestClient(app, base_url="https://localhost") as client:
        response = client.post(
            "/v1/conversations",
            content=b"{}",
            headers={
                "X-LifeOS-Contract-Version": "1.0.0",
                "X-Forwarded-Proto": "https",
                "X-LifeOS-Peer-Fingerprint": "test-peer",
            },
        )
        assert response.status_code == 401


def test_week_plan_preview_via_mtls_is_mutation_free(planning_relay: RunningRelay) -> None:
    sandbox = cast("GoogleCalendarSandbox", planning_relay.sandbox)
    status, body = planning_relay.request("POST", "/v1/week-plan", b"")
    assert status == 200
    proposal = parse_wire_week_plan_proposal(body)
    assert proposal.status is PlanStatus.FEASIBLE
    assert sum(block.duration_minutes for block in proposal.blocks) == 10_080
    assert proposal.gaps == ()
    assert sandbox.mutation_count == 0


def test_week_plan_approval_applies_once_via_mtls(planning_relay: RunningRelay) -> None:
    sandbox = cast("GoogleCalendarSandbox", planning_relay.sandbox)
    status, body = planning_relay.request("POST", "/v1/week-plan", b"")
    assert status == 200
    proposal = parse_wire_week_plan_proposal(body)
    with EncryptedStateStore.open(
        planning_relay.path, planning_relay.keys, planning_relay.clock
    ) as store:
        approval = approval_for(planning_relay.clock, store, proposal.proposal_id)
    envelope = WeekPlanApproval(approval=approval).model_dump_json().encode()
    target = f"/v1/week-plan/{proposal.proposal_id}/approve"
    status, body = planning_relay.request("POST", target, envelope)
    assert status == 200
    result = WeekPlanExecutionResult.model_validate_json(body)
    assert result.proposal_id == proposal.proposal_id
    assert result.state is ProposalState.APPLIED
    assert result.applied_operations == 1
    assert len(sandbox.proposed_events) == 1
    mutation_count = sandbox.mutation_count
    # Replaying the exact approved payload is stale, not silently idempotent.
    assert planning_relay.request("POST", target, envelope)[0] == 409
    assert sandbox.mutation_count == mutation_count


def test_week_plan_approval_from_foreign_device_is_403(planning_relay: RunningRelay) -> None:
    sandbox = cast("GoogleCalendarSandbox", planning_relay.sandbox)
    status, body = planning_relay.request("POST", "/v1/week-plan", b"")
    assert status == 200
    proposal = parse_wire_week_plan_proposal(body)
    with EncryptedStateStore.open(
        planning_relay.path, planning_relay.keys, planning_relay.clock
    ) as store:
        foreign = approval_for(
            planning_relay.clock, store, proposal.proposal_id, DeviceId(str(uuid4()))
        )
    envelope = WeekPlanApproval(approval=foreign).model_dump_json().encode()
    target = f"/v1/week-plan/{proposal.proposal_id}/approve"
    assert planning_relay.request("POST", target, envelope)[0] == 403
    assert sandbox.mutation_count == 0


def test_week_plan_unknown_proposal_is_404(planning_relay: RunningRelay) -> None:
    status, body = planning_relay.request("POST", "/v1/week-plan", b"")
    assert status == 200
    proposal = parse_wire_week_plan_proposal(body)
    with EncryptedStateStore.open(
        planning_relay.path, planning_relay.keys, planning_relay.clock
    ) as store:
        approval = approval_for(planning_relay.clock, store, proposal.proposal_id)
    envelope = WeekPlanApproval(approval=approval).model_dump_json().encode()
    missing = RecordId(uuid4())
    assert planning_relay.request("POST", f"/v1/week-plan/{missing}/approve", envelope)[0] == 404


def test_week_plan_unconfigured_relay_fails_closed(relay: RunningRelay) -> None:
    assert relay.request("POST", "/v1/week-plan", b"")[0] == 501
    assert relay.request("POST", f"/v1/week-plan/{uuid4()}/approve", b"{}")[0] == 501
