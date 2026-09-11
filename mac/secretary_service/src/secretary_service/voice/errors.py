"""Typed failures for the owned voice relay boundary."""

from typing import final, override


@final
class VoiceAccessError(Exception):
    """A voice session or token failed an access rule."""

    def __init__(self, reason: str) -> None:
        """Capture a stable denial reason without sensitive payloads."""
        super().__init__(reason)
        self.reason = reason

    @override
    def __str__(self) -> str:
        return self.reason


@final
class VoiceOrderError(Exception):
    """A realtime event violated ordering or turn state."""

    def __init__(self, reason: str) -> None:
        """Capture the ordering violation."""
        super().__init__(reason)
        self.reason = reason

    @override
    def __str__(self) -> str:
        return self.reason


@final
class ProviderUnavailableError(Exception):
    """The authorized realtime provider could not start or continue."""

    def __init__(self, provider: str) -> None:
        """Identify the unavailable provider at the handling boundary."""
        super().__init__(provider)
        self.provider = provider

    @override
    def __str__(self) -> str:
        return f"realtime provider unavailable: {self.provider}"


@final
class TranscriptStoreError(Exception):
    """The encrypted transcript journal could not be opened."""

    def __init__(self, path: str) -> None:
        """Capture only the local encrypted journal path."""
        super().__init__(path)
        self.path = path

    @override
    def __str__(self) -> str:
        return f"encrypted transcript journal unavailable: {self.path}"


@final
class RecoveryEnvelopeError(Exception):
    """Root-key rotation lacked a verified offline recovery envelope."""

    def __init__(self, reference: str) -> None:
        """Capture the opaque invalid recovery-envelope reference."""
        super().__init__(reference)
        self.reference = reference

    @override
    def __str__(self) -> str:
        return f"offline recovery envelope is not verified: {self.reference}"
