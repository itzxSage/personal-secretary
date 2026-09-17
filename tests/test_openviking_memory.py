"""Integration tests for the OpenViking-backed LifeMemory provider.

These tests start a real OpenViking 0.4.9 server on a loopback port with the
Python 3.13 xxhash patch applied, then exercise the provider end-to-end.
They are skipped when Ollama (the embedding backend) is not reachable.
"""

from __future__ import annotations

import gc
import json
import os
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from openviking_sdk import SyncHTTPClient

from secretary_service.memory import (
    ConfidenceState,
    LifeMemory,
    MemoryEntry,
    MemoryEntryNotFoundError,
)
from secretary_service.memory.openviking import (
    OpenVikingLifeMemoryProvider,
    OpenVikingUnavailableError,
)
from tests.helpers import FakeClock

if TYPE_CHECKING:
    from collections.abc import Iterator

_REPO_ROOT = Path(__file__).resolve().parent.parent
_OLLAMA_PORT = 11434
# Cold Ollama model load + embedding warmup can exceed a minute (observed:
# healthy boot took ~40s idle and blew past 60s under load), so allow headroom.
_SERVER_START_TIMEOUT_S = 180
_SERVER_STOP_TIMEOUT_S = 10
_SERVER_LOG_TAIL_CHARS = 2000
_SERVER_BOOTSTRAP = (
    "import tests.openviking_server_patch; from openviking_cli.server_bootstrap import main; main()"
)


class _ServerStartError(RuntimeError):
    """Raised when the OpenViking server fails to become healthy."""

    def __init__(self, log_tail: str = "") -> None:
        """Record the failure with an optional tail of captured server output."""
        message = "OpenViking server did not become healthy"
        if log_tail:
            message = f"{message}: {log_tail}"
        super().__init__(message)


def _server_log_tail(proc: subprocess.Popen[str]) -> str:
    """Return a short tail of captured server output without blocking.

    The fixture pipes server stdout; on a startup timeout the pipe is still
    open, so a plain read would block. Drain only what is already available.
    """
    if proc.stdout is None:
        return ""
    try:
        fd = proc.stdout.fileno()
    except (OSError, ValueError):
        return ""
    try:
        os.set_blocking(fd, False)
        try:
            drained = cast("str | None", proc.stdout.read())
        finally:
            os.set_blocking(fd, True)
    except (OSError, ValueError):
        return ""
    chunk = drained or ""
    if not chunk:
        return ""
    return chunk[-_SERVER_LOG_TAIL_CHARS:]


def _ollama_available() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(("127.0.0.1", _OLLAMA_PORT)) == 0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return cast("int", sock.getsockname()[1])


pytestmark = [
    pytest.mark.skipif(not _ollama_available(), reason="Ollama is not running on 127.0.0.1:11434"),
    # openviking-sdk 0.1.11 multiplexes all I/O over a module-global daemon
    # worker loop; idle keep-alive transports are reaped by GC at arbitrary
    # points and trip filterwarnings=error via pytest's unraisable plugin.
    # Every resource owned by this module (fixture/dead clients, probe
    # sockets, server pipes) is closed explicitly; only the third-party pool
    # teardown race is ignored here.
    pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning"),
]


@pytest.fixture(scope="module")
def openviking_client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[SyncHTTPClient]:
    """Start a patched OpenViking server and yield a connected client."""
    port = _free_port()
    workspace = tmp_path_factory.mktemp("ov-data")
    config = {
        "server": {"host": "127.0.0.1", "port": port, "auth_mode": "dev"},
        "storage": {"workspace": str(workspace)},
        "embedding": {
            "dense": {
                "provider": "ollama",
                "model": "nomic-embed-text",
                "dimension": 768,
            }
        },
    }
    config_path = tmp_path_factory.mktemp("ov-conf") / "ov.conf"
    _ = config_path.write_text(json.dumps(config), encoding="utf-8")
    env = {
        **os.environ,
        "PYTHONPATH": str(_REPO_ROOT),
        "OPENVIKING_CONFIG_FILE": str(config_path),
    }
    proc = subprocess.Popen(  # noqa: S603 - static command, no untrusted input
        [
            sys.executable,
            "-c",
            _SERVER_BOOTSTRAP,
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = SyncHTTPClient(url=f"http://127.0.0.1:{port}")
    client.initialize()
    try:
        deadline = time.monotonic() + _SERVER_START_TIMEOUT_S
        while time.monotonic() < deadline:
            if client.health():
                break
            time.sleep(1)
        else:
            raise _ServerStartError(_server_log_tail(proc))
        yield client
    finally:
        client.close()
        proc.terminate()
        try:
            _ = proc.wait(timeout=_SERVER_STOP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            _ = proc.wait(timeout=_SERVER_STOP_TIMEOUT_S)
        if proc.stdout is not None:
            proc.stdout.close()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            assert sock.connect_ex(("127.0.0.1", port)) != 0
        # Reap GC debris from the SDK worker loop now, while this module's
        # filterwarnings mark still applies (see pytestmark rationale):
        # pytest's unraisable plugin also collects at session end, outside
        # any item's warning filters, where the same debris would fail the
        # run despite all tests passing.
        for _ in range(3):
            _ = gc.collect()


def _clock() -> FakeClock:
    return FakeClock(datetime(2026, 9, 16, 12, 0, tzinfo=UTC))


def _provider(
    client: SyncHTTPClient,
    user: str,
    clock: FakeClock | None = None,
) -> OpenVikingLifeMemoryProvider:
    return OpenVikingLifeMemoryProvider(
        client, user=user, clock=clock.now if clock is not None else None
    )


def test_provider_satisfies_life_memory_protocol(
    openviking_client: SyncHTTPClient,
) -> None:
    provider = _provider(openviking_client, "protocol")
    assert isinstance(provider, LifeMemory)


def test_remember_recall_roundtrip(openviking_client: SyncHTTPClient) -> None:
    provider = _provider(openviking_client, "roundtrip")
    entry = provider.remember("I prefer dark mode", tags=["preference"])
    assert isinstance(entry, MemoryEntry)
    assert entry.confidence is ConfidenceState.OBSERVED
    assert entry.provenance.source == "user"
    recalled = provider.recall("dark mode", k=10)
    assert [item.id for item in recalled] == [entry.id]
    assert recalled[0].text == "I prefer dark mode"
    assert recalled[0].tags == ("preference",)


def test_correction_confirms_and_records_provenance(
    openviking_client: SyncHTTPClient,
) -> None:
    clock = _clock()
    provider = _provider(openviking_client, "correction", clock)
    entry = provider.remember("I prefer dark mode", tags=["preference"])
    clock.advance(timedelta(minutes=1))
    corrected = provider.correct(entry.id, "I prefer light mode", source="user")
    assert corrected.confidence is ConfidenceState.CONFIRMED
    assert corrected.text == "I prefer light mode"
    assert corrected.updated_at > entry.updated_at
    assert len(corrected.corrections) == 1
    assert corrected.corrections[0].previous_confidence is ConfidenceState.OBSERVED
    assert corrected.corrections[0].new_confidence is ConfidenceState.CONFIRMED
    recalled = provider.recall("light mode", k=10)
    assert recalled[0].text == "I prefer light mode"


def test_contradictory_correction_marks_conflicted(
    openviking_client: SyncHTTPClient,
) -> None:
    provider = _provider(openviking_client, "conflicted")
    entry = provider.remember("I prefer dark mode", tags=["preference"])
    corrected = provider.correct(entry.id, "I prefer light mode", contradicts=True)
    assert corrected.confidence is ConfidenceState.CONFLICTED
    assert corrected.corrections[0].new_confidence is ConfidenceState.CONFLICTED


def test_forget_removes_entry(openviking_client: SyncHTTPClient) -> None:
    provider = _provider(openviking_client, "forget")
    entry = provider.remember("I prefer dark mode")
    provider.forget(entry.id)
    assert provider.recall("dark mode", k=10) == []
    with pytest.raises(MemoryEntryNotFoundError):
        provider.forget(entry.id)


def test_correct_missing_entry_raises(openviking_client: SyncHTTPClient) -> None:
    provider = _provider(openviking_client, "missing")
    with pytest.raises(MemoryEntryNotFoundError):
        _ = provider.correct("missing", "correction")


def test_search_by_tags(openviking_client: SyncHTTPClient) -> None:
    provider = _provider(openviking_client, "tags")
    _ = provider.remember("I prefer dark mode", tags=["preference", "appearance"])
    _ = provider.remember("I run on Tuesdays", tags=["routine"])
    matches = provider.search("", tags=["preference"])
    assert len(matches) == 1
    assert matches[0].text == "I prefer dark mode"


def test_server_down_raises_unavailable() -> None:
    dead_client = SyncHTTPClient(url="http://127.0.0.1:1")
    dead_client.initialize()
    try:
        with pytest.raises(OpenVikingUnavailableError):
            _ = OpenVikingLifeMemoryProvider(dead_client, user="dead")
    finally:
        dead_client.close()
