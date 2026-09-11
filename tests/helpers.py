from datetime import datetime, timedelta
from typing import final


@final
class FakeClock:
    def __init__(self, current: datetime) -> None:
        self._current = current

    def now(self) -> datetime:
        return self._current

    @classmethod
    def from_isoformat(cls, value: str) -> "FakeClock":
        return cls(datetime.fromisoformat(value))

    def advance(self, delta: timedelta) -> None:
        self._current += delta
