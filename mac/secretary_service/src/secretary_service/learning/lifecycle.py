"""Clock and narrowly scoped deletion authorization for learning."""

from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import datetime
from typing import final

from secretary_service.memory import Clock


@final
class LearningLifecycle:
    """Clock and narrowly scoped physical-deletion authorization."""

    def __init__(
        self,
        clock: Clock,
        authorize_delete: Callable[[], None],
        revoke_delete: Callable[[], None],
    ) -> None:
        """Bind retention time and the store deletion gate."""
        self._clock = clock
        self._authorize_delete = authorize_delete
        self._revoke_delete = revoke_delete

    def now(self) -> datetime:
        """Return the current retention time."""
        return self._clock.now()

    @contextmanager
    def deletion(self) -> Generator[None]:
        """Authorize physical deletion only for one bounded operation."""
        self._authorize_delete()
        try:
            yield
        finally:
            self._revoke_delete()
