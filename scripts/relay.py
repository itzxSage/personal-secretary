#!/usr/bin/env python3
"""Explicit local staging setup and single-process mTLS conversation relay."""

import argparse
import ssl
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding

from secretary_service.canonical import CanonicalIdentity
from secretary_service.conversation_api import ConversationDevice, DeviceKind
from secretary_service.enrollment import DeviceId, DeviceRegistry
from secretary_service.keys import MacOSKeychainKeyProvider
from secretary_service.models import ActorId, CorrelationId, RecordId, TransitionContext
from secretary_service.relay_api import create_relay_app
from secretary_service.relay_tls import PeerBoundH11Protocol, certificate_fingerprint
from secretary_service.storage import EncryptedStateStore
from secretary_service.week_planning_live import live_week_planning_factory


class SystemClock:
    """Aware UTC time for request freshness and local setup attribution."""

    @staticmethod
    def now() -> datetime:
        """Return current UTC time."""
        return datetime.now(UTC)


def _serve_arguments(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    serve = commands.add_parser(
        "serve", help="run mTLS ingress; live week planning requires an explicit opt-in"
    )
    _ = serve.add_argument(
        "--enable-week-planning",
        action="store_true",
        help="enable real Google Calendar previews and device-approved week-plan execution",
    )
    _ = serve.add_argument("--planning-timezone", default="America/Chicago")
    _ = serve.add_argument("--host", default="127.0.0.1")
    _ = serve.add_argument("--port", type=int, default=8443)
    _ = serve.add_argument("--server-cert", required=True, type=Path)
    _ = serve.add_argument("--server-key", required=True, type=Path)
    _ = serve.add_argument("--client-ca", required=True, type=Path)


def main() -> None:
    """Require explicit Keychain-backed setup; never enroll fixture keys automatically."""
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--state", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    provision = commands.add_parser(
        "provision", help="locally bind one enrolled device to its user"
    )
    _ = provision.add_argument("--device-id", required=True, type=UUID)
    _ = provision.add_argument("--participant-id", required=True, type=UUID)
    _ = provision.add_argument("--name", required=True)
    _ = provision.add_argument("--client-cert", required=True, type=Path)
    _ = provision.add_argument(
        "--signing-public-key",
        required=True,
        type=Path,
        help="file containing 32-byte Ed25519 public key as hex",
    )
    _serve_arguments(commands)
    _ = commands.add_parser("prune", help="purge conversations past the fixed retention deadline")
    args = parser.parse_args()
    clock = SystemClock()
    keys = MacOSKeychainKeyProvider()
    if args.command == "provision":
        certificate = x509.load_pem_x509_certificate(args.client_cert.read_bytes())
        fingerprint = certificate_fingerprint(certificate.public_bytes(Encoding.DER))
        public_key = args.signing_public_key.read_text(encoding="ascii").strip()
        with EncryptedStateStore.open(args.state, keys, clock) as store:
            registry = DeviceRegistry(clock, store.devices)
            device_id = DeviceId(str(args.device_id))
            existing = registry.device(device_id)
            if existing is None:
                _ = registry.enroll(
                    device_id, fingerprint, ActorId("local-setup"), approval_public_key=public_key
                )
            else:
                _ = registry.verify_mtls_identity(device_id, fingerprint)
                if existing.approval_public_key != public_key:
                    parser.error("device already has a different signing key")
            identities = store.canonical_records().identities
            identity = next(
                (value for value in identities if value.record_id == args.participant_id), None
            )
            if identity is None:
                identity = CanonicalIdentity(
                    record_id=RecordId(args.participant_id),
                    created_at=clock.now(),
                    state="active",
                    display_name=args.name,
                    is_primary=not identities,
                )
            binding = store.conversations.device(args.device_id)
            if binding is not None and binding.participant_id != args.participant_id:
                parser.error("device is already bound to another participant")
            store.conversations.provision(
                identity,
                binding
                or ConversationDevice(
                    device_id=args.device_id,
                    participant_id=args.participant_id,
                    kind=DeviceKind.IOS,
                    name=args.name,
                    created_at=clock.now(),
                ),
                TransitionContext(
                    actor=ActorId("local-setup"),
                    correlation_id=CorrelationId(str(uuid4())),
                    occurred_at=clock.now(),
                ),
            )
        print("Device bound to encrypted conversation state. Production remains disabled.")
        return
    if args.command == "prune":
        with EncryptedStateStore.open(args.state, keys, clock) as store:
            count = store.conversations.expire(
                clock.now(),
                TransitionContext(
                    actor=ActorId("retention"),
                    correlation_id=CorrelationId(str(uuid4())),
                    occurred_at=clock.now(),
                ),
            )
        print(f"Expired conversations purged: {count}")
        return
    try:
        _ = ZoneInfo(args.planning_timezone)
    except ZoneInfoNotFoundError:
        parser.error("unknown planning timezone")
    app = create_relay_app(
        lambda: EncryptedStateStore.open(args.state, keys, clock),
        clock,
        week_planning_factory=(
            live_week_planning_factory(clock, keys) if args.enable_week_planning else None
        ),
        planning_timezone=args.planning_timezone,
    )
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        http=PeerBoundH11Protocol,
        ws="none",
        ssl_certfile=str(args.server_cert),
        ssl_keyfile=str(args.server_key),
        ssl_ca_certs=str(args.client_ca),
        ssl_cert_reqs=ssl.CERT_REQUIRED,
        proxy_headers=False,
        access_log=False,
        workers=1,
        limit_concurrency=16,
        timeout_keep_alive=5,
    )


if __name__ == "__main__":
    main()
