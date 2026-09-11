#!/usr/bin/env python3
"""Exercise the local LifeOS slices and report native QA separately."""

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.verification import (
    ROOT,
    checks_report,
    run_check,
)


def main() -> int:
    """Run HTTP calendar, voice, recovery and Swift checks without live accounts."""
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--ios-simulator")
    _ = parser.add_argument("--openclaw-fixture", default="fixtures/openclaw")
    _ = parser.add_argument("--calendar-sandbox", default="fixtures/google")
    _ = parser.add_argument("--run-id", default="lifeos-local")
    args = parser.parse_args()
    run_id = str(args.run_id)
    if not run_id or any(
        character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        for character in run_id
    ):
        parser.error("run-id must contain only letters, digits, hyphens and underscores")
    output = ROOT / "artifacts" / "verification" / run_id / "F3"
    commands = [
        (
            "tomorrow-http",
            [
                "bash",
                "scripts/e2e_tomorrow_plan.sh",
                "--calendar-sandbox",
                str(args.calendar_sandbox),
                f"{run_id}/F3/tomorrow",
            ],
        ),
        (
            "voice",
            [
                "bash",
                "scripts/e2e_voice_conversation.sh",
                "--adapter-fixture",
                str(args.openclaw_fixture),
                f"{run_id}/F3/voice",
            ],
        ),
        (
            "recovery",
            [
                "uv",
                "run",
                "pytest",
                "-q",
                "tests/test_rollout_recovery.py",
                "tests/test_recovery_restore.py",
            ],
        ),
        ("swift", ["swift", "test", "--package-path", "ios/SecretaryApp"]),
        (
            "conversation-mtls",
            [
                "uv",
                "run",
                "pytest",
                "-q",
                "tests/test_relay_https.py",
                "tests/test_conversation_relay.py",
            ],
        ),
        (
            "calendar-live-contract",
            [
                "uv",
                "run",
                "pytest",
                "-q",
                "tests/test_google_calendar.py",
                "tests/test_google_calendar_live.py",
            ],
        ),
        ("swift-build", ["swift", "build", "--package-path", "ios/SecretaryApp"]),
    ]
    checks = [run_check(name, command, output) for name, command in commands]
    native = "not_requested"
    if args.ios_simulator:
        checks.append(
            run_check(
                "simulator-toolchain", ["xcrun", "simctl", "list", "devices", "available"], output
            )
        )
        # A generated project and SwiftPM run are not an installed iOS application/UI test.
        # Fail until signing, the UI harness and captured-device checks exist.
        native = "unverified: signed iOS app/UI test harness and device evidence required"
    passed = checks_report(
        output / "report.json",
        checks,
        native_qa=native,
        scope="hermetic fixtures; no live Google, OpenAI or OpenClaw connection",
        required_evidence_complete=not bool(args.ios_simulator),
    )
    print(f"LifeOS E2E {'passed' if passed else 'incomplete'}: {output / 'report.json'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
