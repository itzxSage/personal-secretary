"""Shared release-check execution with timeouts and reproducible source evidence."""

import hashlib
import json
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from scripts.scan_secrets import source_files

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Check:
    """One command's observed outcome, linked to its local log."""

    name: str
    command: list[str]
    exit_code: int
    seconds: float
    log: str


def source_digest() -> str:
    """Hash names and contents so uncommitted work is included in the evidence."""
    digest = hashlib.sha256()
    for path in source_files(ROOT):
        if path.is_symlink():
            continue
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def run_check(name: str, command: list[str], output: Path, timeout: int = 600) -> Check:
    """Execute a fixed local check and retain diagnostics outside source control."""
    output.mkdir(parents=True, exist_ok=True)
    log = output / f"{name}.log"
    started = time.monotonic()
    with log.open("w", encoding="utf-8") as handle:
        try:
            result = subprocess.run(  # noqa: S603 - caller supplies fixed argument lists
                command,
                cwd=ROOT,
                stdout=handle,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
            code = result.returncode
        except subprocess.TimeoutExpired:
            _ = handle.write("\nCHECK TIMED OUT\n")
            code = 124
        except OSError as error:
            _ = handle.write(f"check could not start: {error}\n")
            code = 127
    return Check(name, command, code, round(time.monotonic() - started, 2), str(log))


def write_report(path: Path, payload: dict[str, object]) -> None:
    """Write machine-readable evidence without raw application content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def checks_report(path: Path, checks: list[Check], **details: object) -> bool:
    """Report actual command outcomes; missing tools and failures never pass."""
    passed = (
        bool(checks)
        and all(check.exit_code == 0 for check in checks)
        and details.get("required_evidence_complete", True) is True
    )
    write_report(
        path,
        {
            "schema_version": 1,
            "verdict": "APPROVE" if passed else "REJECT",
            "source_sha256": source_digest(),
            "checks": [asdict(check) for check in checks],
            **details,
        },
    )
    return passed
