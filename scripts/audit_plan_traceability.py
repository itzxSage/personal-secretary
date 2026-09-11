#!/usr/bin/env python3
"""Map the approved plan to fresh test evidence without trusting completion boxes."""

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.verification import (
    ROOT,
    source_digest,
    write_report,
)

TASK_TESTS = {
    1: ["test_graft_matrix"],
    2: ["test_migration"],
    3: ["test_authority", "test_device_enrollment", "test_approval_signatures"],
    4: ["test_conversation_contract"],
    5: ["test_planner"],
    6: ["test_goals_memory"],
    7: ["test_google_calendar"],
    8: ["test_tomorrow_slice"],
    9: ["test_openclaw_adapter", "test_openclaw_adversary"],
    10: ["test_delegation"],
    11: ["test_conversation_contract"],
    12: ["test_voice_session", "test_voice_privacy"],
    13: ["test_voice_conversation"],
    14: ["test_energy"],
    15: ["test_channel_continuity", "test_telegram_adapter"],
    16: ["test_learning"],
    17: ["test_worker_sandbox", "test_autopilot_policy"],
    18: ["test_rollout_recovery", "test_recovery_restore"],
}


def main() -> int:  # noqa: C901 - independent evidence checks accumulate every failure
    """Require current test execution and explicit remaining release evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("plan", type=Path)
    _ = parser.add_argument("evidence", type=Path)
    args = parser.parse_args()
    digest = source_digest()
    problems: list[str] = []
    text = args.plan.read_text(encoding="utf-8")
    tasks = {
        int(number): title
        for number, title in re.findall(r"^- \[[ x]\] (\d+)\. (.+)$", text, re.MULTILINE)
    }
    if set(tasks) != set(TASK_TESTS):
        problems.append("plan does not contain exactly the expected 18 implementation tasks")
    executed: set[str] = set()
    try:
        qa = json.loads((args.evidence / "F2/report.json").read_text(encoding="utf-8"))
        if qa["source_sha256"] != digest or qa["verdict"] != "APPROVE":
            problems.append("F2 evidence failed or describes a different source snapshot")
        tree = ET.parse(args.evidence / "F2/pytest.xml")  # noqa: S314 - locally generated pytest XML
        for case in tree.iter("testcase"):
            if not any(case.find(tag) is not None for tag in ("failure", "error", "skipped")):
                executed.add(case.attrib.get("classname", "").split(".")[-1])
    except (OSError, ValueError, KeyError, ET.ParseError):
        problems.append("current Python test evidence is missing or invalid")
    rows = []
    for number, modules in TASK_TESTS.items():
        missing = sorted(set(modules) - executed)
        if missing:
            problems.append(f"task {number} lacks passing tests: {', '.join(missing)}")
        rows.append(
            {
                "task": number,
                "title": tasks.get(number),
                "test_modules": modules,
                "local_python_tests": "passed" if not missing else "unverified",
            }
        )
    # These are explicit product-delivery requirements, not assertions inferred
    # from Python or Swift unit-test success. Each must gain fresh evidence.
    requirements = json.loads((ROOT / "docs/release-requirements.json").read_text(encoding="utf-8"))
    for requirement in requirements:
        report_name = requirement["evidence"]
        try:
            report = json.loads((args.evidence / report_name).read_text(encoding="utf-8"))
            valid = report.get("source_sha256") == digest and report.get("verdict") == "APPROVE"
        except (OSError, ValueError):
            valid = False
        if not valid:
            problems.append(requirement["reason"])
    write_report(
        args.evidence / "F1/report.json",
        {
            "schema_version": 1,
            "source_sha256": digest,
            "verdict": "REJECT" if problems else "APPROVE",
            "tasks": rows,
            "remaining_requirements": problems,
            "qualification": "local regression mapping; separate release evidence required",
        },
    )
    print(json.dumps({"remaining_requirements": problems}, indent=2))
    return int(bool(problems))


if __name__ == "__main__":
    sys.exit(main())
