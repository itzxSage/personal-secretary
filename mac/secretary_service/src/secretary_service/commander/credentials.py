"""Fail-closed default credential broker for Commander workers."""

from typing import final, override

from secretary_service.commander.contracts import CredentialGrant, WorkerRequest


@final
class CredentialUnavailableError(Exception):
    """A worker requested a credential but no broker was configured."""

    def __init__(self, reference: str) -> None:
        """Record only the unavailable opaque reference."""
        super().__init__(reference)
        self.reference = reference

    @override
    def __str__(self) -> str:
        return f"credential reference is unavailable: {self.reference}"


@final
class NoCredentialBroker:
    """Default broker that permits only requests requiring no credentials."""

    def issue(self, request: WorkerRequest) -> tuple[CredentialGrant, ...]:
        """Fail closed when a request names any credential reference."""
        if request.credential_references:
            raise CredentialUnavailableError(request.credential_references[0])
        return ()
