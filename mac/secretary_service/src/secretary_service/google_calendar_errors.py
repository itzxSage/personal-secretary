"""Typed Google Calendar adapter failures."""

from typing import final, override


class CalendarError(Exception):
    """Base failure carrying a non-sensitive reason."""

    reason: str

    def __init__(self, reason: str) -> None:
        """Create a failure containing no provider payload or credential."""
        super().__init__(reason)
        self.reason = reason

    @override
    def __str__(self) -> str:
        return self.reason


@final
class CalendarAuthorizationError(CalendarError):
    """OAuth or proposal authority rejected the operation."""


@final
class CalendarContractError(CalendarError):
    """Provider output contradicted the requested operation."""


@final
class CalendarTransientError(CalendarError):
    """Retryable provider failure occurred before mutation."""


@final
class CalendarInterruptedError(ConnectionError):
    """Provider committed a mutation but its response was interrupted."""

    def __init__(self, reason: str) -> None:
        """Create an ambiguous-outcome transport failure."""
        super().__init__(reason)
        self.reason = reason

    @override
    def __str__(self) -> str:
        return self.reason


@final
class StaleSyncTokenError(CalendarError):
    """Incremental sync token requires a new full synchronization."""
