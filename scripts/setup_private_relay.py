#!/usr/bin/env python3
"""Create a private, short-lived staging relay identity bundle."""

import argparse
import base64
import hashlib
import ipaddress
import json
import os
import secrets
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import keyring
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _certificate(  # noqa: PLR0913
    subject: x509.Name,
    issuer: x509.Name,
    public_key: object,
    issuer_key: rsa.RSAPrivateKey,
    *,
    is_ca: bool,
    usages: list[x509.ObjectIdentifier] | None = None,
    sans: list[x509.GeneralName] | None = None,
) -> x509.Certificate:
    now = datetime.now(UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(
            x509.BasicConstraints(ca=is_ca, path_length=0 if is_ca else None), critical=True
        )
    )
    if usages:
        builder = builder.add_extension(x509.ExtendedKeyUsage(usages), critical=False)
    if sans:
        builder = builder.add_extension(x509.SubjectAlternativeName(sans), critical=False)
    return builder.sign(issuer_key, hashes.SHA256())


def _write(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    path.chmod(0o600)


def _ensure_service_keys() -> None:
    service = "com.personal-secretary.service"
    for account in ("database-key", "audit-key", "backup-wrapping-key"):
        if keyring.get_password(service, account) is None:
            keyring.set_password(service, account, base64.b64encode(os.urandom(32)).decode("ascii"))


def main() -> None:
    """Generate certificates, request keys, identifiers, and service keys."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--name", default="JR's iPhone")
    args = parser.parse_args()

    target: Path = args.directory.expanduser().resolve()
    if target.exists():
        parser.error(f"refusing to overwrite existing directory: {target}")
    target.mkdir(parents=True, mode=0o700)
    target.chmod(0o700)

    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = _name("Personal Secretary staging CA")
    ca_cert = _certificate(ca_name, ca_name, ca_key.public_key(), ca_key, is_ca=True)

    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_name = _name("Personal Secretary staging relay")
    server_sans: list[x509.GeneralName] = [x509.DNSName(socket.gethostname())]
    try:
        server_sans.append(x509.IPAddress(ipaddress.ip_address(args.host)))
    except ValueError:
        server_sans.append(x509.DNSName(args.host))
    server_cert = _certificate(
        server_name,
        ca_name,
        server_key.public_key(),
        ca_key,
        is_ca=False,
        usages=[ExtendedKeyUsageOID.SERVER_AUTH],
        sans=server_sans,
    )

    client_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    client_name = _name("Personal Secretary staging iPhone")
    client_cert = _certificate(
        client_name,
        ca_name,
        client_key.public_key(),
        ca_key,
        is_ca=False,
        usages=[ExtendedKeyUsageOID.CLIENT_AUTH],
    )
    signing_key = ed25519.Ed25519PrivateKey.generate()
    signing_private = signing_key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    signing_public = signing_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    p12_password = secrets.token_urlsafe(32).encode("ascii")
    client_p12 = pkcs12.serialize_key_and_certificates(
        b"Staging relay client",
        client_key,
        client_cert,
        [ca_cert],
        serialization.BestAvailableEncryption(p12_password),
    )

    pem = serialization.Encoding.PEM
    traditional = serialization.PrivateFormat.TraditionalOpenSSL
    no_encryption = serialization.NoEncryption()
    _write(target / "ca.pem", ca_cert.public_bytes(pem))
    _write(target / "server.pem", server_cert.public_bytes(pem))
    _write(target / "server-key.pem", server_key.private_bytes(pem, traditional, no_encryption))
    _write(target / "client.pem", client_cert.public_bytes(pem))
    _write(target / "client.p12", client_p12)
    _write(target / "client-p12-password", p12_password)
    _write(target / "request-signing-key.b64", base64.b64encode(signing_private))
    _write(target / "request-signing-public.hex", signing_public.hex().encode("ascii"))

    metadata = {
        "origin": f"https://{args.host}:{args.port}",
        "host": args.host,
        "port": args.port,
        "conversationID": str(uuid4()),
        "participantID": str(uuid4()),
        "deviceID": str(uuid4()),
        "deviceName": args.name,
        "serverCertificateSHA256": hashlib.sha256(
            server_cert.public_bytes(serialization.Encoding.DER)
        ).hexdigest(),
    }
    _write(target / "pairing.json", json.dumps(metadata, indent=2).encode("utf-8") + b"\n")
    _ensure_service_keys()
    print(f"Created private staging relay material in {target}")


if __name__ == "__main__":
    main()
