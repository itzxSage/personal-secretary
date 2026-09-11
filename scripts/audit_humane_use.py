#!/usr/bin/env python3
"""Run authority and humane-use regression checks over the current local engine."""

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.verification import (
    ROOT,
    checks_report,
    run_check,
)

AUTHORITY_TESTS = [
    "tests/security",
    "tests/test_authority.py",
    "tests/test_device_enrollment.py",
    "tests/test_autopilot_policy.py",
    "tests/test_worker_sandbox.py",
    "tests/test_openclaw_adversary.py",
    "tests/test_rollout_recovery.py",
]
ENERGY_TESTS = ["tests/test_energy.py", "tests/test_learning.py"]


def main() -> int:
    """Require the declared deny-by-default scope and execute its regression cases."""
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("authority", type=Path)
    _ = parser.add_argument("voice", type=Path)
    _ = parser.add_argument("energy", type=Path)
    _ = parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts/verification/lifeos-local/F4"
    )
    args = parser.parse_args()
    authority = json.loads((args.authority / "scope.json").read_text(encoding="utf-8"))
    energy = json.loads((args.energy / "scope.json").read_text(encoding="utf-8"))
    if (
        authority.get("production_capabilities_enabled") is not False
        or energy.get("clinical_inference_allowed") is not False
        or authority.get("required_tests") != AUTHORITY_TESTS
        or energy.get("required_tests") != ENERGY_TESTS
        or not (args.voice / "rest-of-day.pcm").is_file()
    ):
        parser.error("scope fixtures are missing or contradict the required policy checks")
    checks = [
        run_check(
            "authority-energy-privacy",
            [
                "uv",
                "run",
                "pytest",
                "-q",
                *AUTHORITY_TESTS,
                *ENERGY_TESTS,
                "tests/test_voice_privacy.py",
                "tests/test_voice_conversation.py",
            ],
            args.output,
        ),
        run_check("donor-licenses", ["uv", "run", "scripts/check_licenses.py"], args.output),
    ]
    passed = checks_report(
        args.output / "report.json",
        checks,
        scope="local policy regressions; native invocation and live providers require F1/F3",
    )
    print(f"Humane-use regression audit {'passed' if passed else 'failed'}: {args.output}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
