from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from secretary_service.keys import DeterministicTestKeyProvider
from secretary_service.storage import EncryptedStateStore
from tests.helpers import FakeClock


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--fake-clock",
        action="store",
        default="2026-09-05T14:00:00Z",
        help="Aware ISO-8601 timestamp for deterministic time-based tests.",
    )


@pytest.fixture
def fake_now(request: pytest.FixtureRequest) -> datetime:
    raw = str(request.config.getoption("fake_clock"))
    return datetime.fromisoformat(raw)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime(2026, 1, 1, tzinfo=UTC))


@pytest.fixture
def keys() -> DeterministicTestKeyProvider:
    return DeterministicTestKeyProvider.from_seed(b"hermetic-todo-2")


@pytest.fixture
def store(
    tmp_path: Path,
    keys: DeterministicTestKeyProvider,
    clock: FakeClock,
) -> Iterator[EncryptedStateStore]:
    with EncryptedStateStore.open(tmp_path / "state.sqlite", keys, clock) as state:
        yield state
