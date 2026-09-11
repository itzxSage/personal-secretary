#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = ["pydantic>=2.11,<3"]
# ///

# --- How to run ---
# 1. Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run: uv run scripts/verify_openclaw_pin.py
# 3. The command validates the adapter fixture, source pin, and graft matrix.
# ------------------

"""Verify the reviewed OpenClaw revision and adapter-only compatibility fixture."""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import ClassVar, Final, override

from pydantic import BaseModel, ConfigDict, ValidationError

ROOT: Final = Path(__file__).resolve().parents[1]
EXPECTED_REVISION: Final = "befc0c24"
CONTRACTS: Final = ROOT / "mac/secretary_service/src/secretary_service/openclaw/contracts.py"
FIXTURE: Final = (
    ROOT / "mac/secretary_service/src/secretary_service/openclaw/fixtures/upstream-update.json"
)
MATRIX: Final = ROOT / "docs/capability-graft-matrix.md"
EXPECTED_ENDPOINTS: Final = (
    "conversation.get",
    "conversation.events.list",
    "conversation.events.append",
)
EXPECTED_CAPABILITIES: Final = ("conversation.read", "conversation.write")
EXPECTED_INITIAL_CHANNELS: Final = ("openclaw_webchat", "telegram")
EXPECTED_LATER_CHANNELS: Final = ("imessage", "email")
MATRIX_COLUMN_COUNT: Final = 9
REVISION_COLUMN: Final = 2
SECURITY_COLUMN: Final = 8


class PluginContract(BaseModel):
    """Machine-readable least-privilege adapter contract."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    transport: str
    core_patched: bool
    endpoints: tuple[str, ...]
    capabilities: tuple[str, ...]
    initial_text_channels: tuple[str, ...]
    later_constrained_channels: tuple[str, ...]


class UpstreamUpdateFixture(BaseModel):
    """Reviewed pin and intentionally incompatible update candidate."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    fixture_version: int
    upstream: str
    pinned_revision: str
    candidate_revision: str
    candidate_expected_compatible: bool
    conversation_contract_version: str
    plugin_contract: PluginContract


class PinVerificationError(Exception):
    """Pin source, fixture, or provenance matrix is inconsistent."""

    def __init__(self, path: Path, detail: str) -> None:
        """Record the inconsistent pin source and its structured detail."""
        super().__init__(path, detail)
        self.path = path
        self.detail = detail

    @override
    def __str__(self) -> str:
        return f"{self.path}: {self.detail}"


def load_source_pin(path: Path) -> str:
    """Read the adapter pin from Python syntax without importing project code."""
    try:
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as error:
        raise PinVerificationError(path, f"cannot parse source: {error}") from error
    for node in module.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "OPENCLAW_REVISION"
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            return node.value.value
    raise PinVerificationError(path, "OPENCLAW_REVISION string constant is missing")


def load_fixture(path: Path) -> UpstreamUpdateFixture:
    """Parse the upstream-update fixture through its strict schema."""
    try:
        return UpstreamUpdateFixture.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as error:
        raise PinVerificationError(path, f"invalid update fixture: {error}") from error


def matrix_openclaw_pins(path: Path) -> tuple[tuple[str, str], ...]:
    """Return every OpenClaw donor row's revision and security decision."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise PinVerificationError(path, f"cannot read matrix: {error}") from error
    rows: list[tuple[str, str]] = []
    for line in lines:
        if "| OpenClaw |" not in line:
            continue
        cells = tuple(cell.strip() for cell in line.strip().strip("|").split("|"))
        if len(cells) != MATRIX_COLUMN_COUNT:
            raise PinVerificationError(path, "OpenClaw matrix row must have nine columns")
        rows.append((cells[REVISION_COLUMN], cells[SECURITY_COLUMN]))
    if not rows:
        raise PinVerificationError(path, "no OpenClaw matrix rows found")
    return tuple(rows)


def verify() -> None:
    """Raise with a structured failure if any reviewed boundary fact drifts."""
    source_pin = load_source_pin(CONTRACTS)
    fixture = load_fixture(FIXTURE)
    matrix_rows = matrix_openclaw_pins(MATRIX)
    facts = {
        source_pin == EXPECTED_REVISION,
        fixture.upstream == "OpenClaw",
        fixture.pinned_revision == EXPECTED_REVISION,
        fixture.candidate_revision != EXPECTED_REVISION,
        fixture.candidate_expected_compatible is False,
        fixture.conversation_contract_version == "1.0.0",
        fixture.plugin_contract.transport == "out_of_process_mtls",
        fixture.plugin_contract.core_patched is False,
        fixture.plugin_contract.endpoints == EXPECTED_ENDPOINTS,
        fixture.plugin_contract.capabilities == EXPECTED_CAPABILITIES,
        fixture.plugin_contract.initial_text_channels == EXPECTED_INITIAL_CHANNELS,
        fixture.plugin_contract.later_constrained_channels == EXPECTED_LATER_CHANNELS,
        all(pin == EXPECTED_REVISION for pin, _ in matrix_rows),
        all(security == "adapter-only" for _, security in matrix_rows),
    }
    if facts != {True}:
        raise PinVerificationError(FIXTURE, "OpenClaw pin or compatibility boundary drifted")


def main() -> int:
    """Run pin verification with stable command-line exit behavior."""
    try:
        verify()
    except PinVerificationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"OpenClaw pin verified: {EXPECTED_REVISION} (adapter-only, mTLS)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
