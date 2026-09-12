#!/usr/bin/env python3
"""Run all available final gates and produce a source-bound release report."""

import argparse
import sys
from dataclasses import asdict
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.verification import (
    ROOT,
    checks_report,
    run_check,
    source_digest,
    write_report,
)


def main() -> int:
    """Continue independent checks after failures and keep production disabled."""
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--run-id", default="lifeos-local")
    _ = parser.add_argument("--plan", type=Path, default=ROOT / "docs/lifeos-plan.md")
    _ = parser.add_argument(
        "--with-postgres",
        action="store_true",
        help="require real PostgreSQL contract and crash tests in a temporary local cluster",
    )
    _ = parser.add_argument(
        "--local-only",
        action="store_true",
        help="return success for passing local checks; still report unmet release requirements",
    )
    args = parser.parse_args()
    run_id = str(args.run_id)
    if not run_id or any(
        character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        for character in run_id
    ):
        parser.error("invalid run-id")
    output = ROOT / "artifacts/verification" / run_id
    if output.exists():
        parser.error("run-id already exists; use a new ID to preserve prior evidence")
    f2 = output / "F2"
    commands = [
        ("contracts", ["uv", "run", "scripts/generate_contracts.py", "--check"]),
        ("format", ["uv", "run", "ruff", "format", "--check", "."]),
        ("lint", ["uv", "run", "ruff", "check", "."]),
        ("types", ["uv", "run", "basedpyright"]),
        ("python", ["uv", "run", "pytest", "-q", f"--junitxml={f2 / 'pytest.xml'}"]),
        ("licenses", ["uv", "run", "scripts/check_licenses.py"]),
        ("secrets", ["uv", "run", "python", "-m", "scripts.scan_secrets"]),
        ("openclaw-pin", ["uv", "run", "scripts/verify_openclaw_pin.py"]),
        ("hermes-pin", ["uv", "run", "scripts/verify_hermes_pin.py"]),
    ]
    checks = []
    if args.with_postgres:
        commands.append(
            (
                "postgres",
                ["uv", "run", "scripts/verify_postgres.py", "--junitxml", str(f2 / "postgres.xml")],
            )
        )
    for name, command in commands:
        print(f"Checking {name}...", flush=True)
        checks.append(run_check(name, command, f2))
    f2_passed = checks_report(
        f2 / "report.json", checks, scope="local code and security regression gate"
    )
    f4 = run_check(
        "F4",
        [
            "uv",
            "run",
            "python",
            "-m",
            "scripts.audit_humane_use",
            "fixtures/authority",
            "fixtures/voice",
            "fixtures/energy",
            "--output",
            str(output / "F4"),
        ],
        output,
    )
    f3 = run_check("F3", ["bash", "scripts/e2e_lifeos.sh", "--run-id", run_id], output)
    f1 = run_check(
        "F1",
        [
            "uv",
            "run",
            "python",
            "-m",
            "scripts.audit_plan_traceability",
            str(args.plan),
            str(output),
        ],
        output,
    )
    local_passed = f2_passed and f3.exit_code == 0 and f4.exit_code == 0
    write_report(
        output / "report.json",
        {
            "schema_version": 1,
            "source_sha256": source_digest(),
            "local_verification": "passed" if local_passed else "failed",
            "release_ready": local_passed and f1.exit_code == 0,
            "production_capabilities_enabled": False,
            "postgres_tests_requested": bool(args.with_postgres),
            "final_gates": [asdict(check) for check in (f1, f3, f4)],
            "F2_report": str(f2 / "report.json"),
        },
    )
    print(f"Report: {output / 'report.json'}")
    return 0 if local_passed and (args.local_only or f1.exit_code == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
