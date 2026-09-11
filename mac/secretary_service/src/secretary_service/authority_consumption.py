"""Atomic one-shot authorization consumption in memory or SQLCipher."""

from typing import Protocol, final

from sqlcipher3 import dbapi2 as sqlcipher

type ConsumptionKey = tuple[str, str]


class ConsumptionStore(Protocol):
    """Claim all authorization keys exactly once or leave every key unchanged."""

    def consume(self, keys: tuple[ConsumptionKey, ...]) -> bool:
        """Return false when any key was already consumed."""
        ...


@final
class MemoryConsumptionStore:
    """Process-local implementation for isolated policy unit tests."""

    def __init__(self) -> None:
        """Start without any consumed facts."""
        self._consumed: set[ConsumptionKey] = set()

    def consume(self, keys: tuple[ConsumptionKey, ...]) -> bool:
        """Check all keys before changing state."""
        if any(key in self._consumed for key in keys):
            return False
        self._consumed.update(keys)
        return True


@final
class EncryptedConsumptionStore:
    """Persist replay protection with atomic unique constraints across restarts."""

    def __init__(self, connection: sqlcipher.Connection) -> None:
        """Use the owning encrypted state connection, never a plaintext database."""
        self._connection = connection

    def consume(self, keys: tuple[ConsumptionKey, ...]) -> bool:
        """Commit every marker together; a duplicate rolls the entire claim back."""
        try:
            with self._connection:
                for namespace, identifier in keys:
                    _ = self._connection.execute(
                        "INSERT INTO authority_consumption(namespace, identifier) VALUES (?, ?)",
                        (namespace, identifier),
                    )
        except sqlcipher.IntegrityError:
            return False
        return True
