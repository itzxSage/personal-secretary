"""Uvicorn HTTP/1.1 adapter binding ASGI requests to their real mTLS peer."""

import asyncio
import hashlib
import ssl
from typing import cast, final, override

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from uvicorn._types import ASGIReceiveCallable, ASGISendCallable, Scope
from uvicorn.protocols.http.h11_impl import H11Protocol


def certificate_fingerprint(certificate_der: bytes) -> str:
    """Use SHA256 of DER SubjectPublicKeyInfo, not an HTTP identity header."""
    certificate = x509.load_der_x509_certificate(certificate_der)
    public_key = certificate.public_key().public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo
    )
    return hashlib.sha256(public_key).hexdigest()


@final
class PeerBoundH11Protocol(H11Protocol):
    """Inject a certificate fingerprint per connection; absent TLS always fails closed."""

    @override
    def connection_made(self, transport: asyncio.Transport) -> None:
        """Capture the TLS session's verified client certificate before serving requests."""
        super().connection_made(transport)
        tls = cast("object", transport.get_extra_info("ssl_object"))
        fingerprint: str | None = None
        if (
            isinstance(tls, ssl.SSLObject | ssl.SSLSocket)
            and tls.context.verify_mode == ssl.CERT_REQUIRED
        ):
            certificate = tls.getpeercert(binary_form=True)
            if certificate:
                fingerprint = certificate_fingerprint(certificate)
        original = self.app

        async def bound(scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable) -> None:
            if scope["type"] == "http":
                scope["state"] = {**scope.get("state", {}), "lifeos_peer_fingerprint": fingerprint}
            await original(scope, receive, send)

        self.app = bound
