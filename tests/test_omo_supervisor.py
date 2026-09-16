"""Hermes→OMO read-only supervision: parse-only status summary tests."""

import copy
import inspect
import json
from typing import cast

from secretary_service.hermes import omo_supervisor
from secretary_service.hermes.omo_supervisor import (
    parse_boulder_status,
    parse_ulw_loop_status,
)

# Trimmed mirror of the current .omo/boulder.json (schema_version 2).
BOULDER_FIXTURE: dict[str, object] = {
    "schema_version": 2,
    "active_work_id": "lifeos-continuation-campaign-ac4ca534",
    "works": {
        "relay-boundary-hardening": {
            "work_id": "relay-boundary-hardening",
            "plan_name": "relay-boundary-hardening",
            "status": "completed",
            "started_at": "2026-09-14T05:52:15.564Z",
            "ended_at": "2026-09-14T06:39:12.104Z",
        },
        "lifeos-master-plan-59ad3eeb": {
            "work_id": "lifeos-master-plan-59ad3eeb",
            "plan_name": "lifeos-master-plan",
            "status": "completed",
            "started_at": "2026-09-15T04:37:55.736Z",
            "ended_at": "2026-09-16T05:50:00.000Z",
            "superseded_by": "lifeos-continuation-campaign-ac4ca534",
            "supersession_note": "Superseded by lifeos-continuation-campaign on 2026-09-16",
        },
        "lifeos-continuation-campaign-ac4ca534": {
            "work_id": "lifeos-continuation-campaign-ac4ca534",
            "plan_name": "lifeos-continuation-campaign",
            "status": "active",
            "started_at": "2026-09-16T05:16:14.069Z",
            "updated_at": "2026-09-16T06:29:05.600Z",
            "agent": "atlas",
        },
    },
    "active_plan": (
        "/Users/jrsgagne/Development/personal-secretary/.omo/plans/lifeos-continuation-campaign.md"
    ),
    "plan_name": "lifeos-continuation-campaign",
    "status": "active",
    "started_at": "2026-09-16T05:16:14.069Z",
    "updated_at": "2026-09-16T06:29:05.600Z",
    "agent": "atlas",
}

# Assumed shape of `omo ulw-loop status --json`.
ULW_LOOP_FIXTURE: dict[str, object] = {
    "active": True,
    "plan": "lifeos-continuation-campaign",
    "work_id": "lifeos-continuation-campaign-ac4ca534",
    "attempt_dir": ".omo/run-continuation",
    "attempt": "ses_f57870431ffe56uqAEwG8hWF9u",
    "pending_review": True,
    "session_id": "opencode:ses_f57870431ffe56uqAEwG8hWF9u",
    "status": "running",
    "started_at": "2026-09-16T05:16:14.069Z",
    "updated_at": "2026-09-16T06:29:05.600Z",
}

# Primitives that would violate the read-only contract if present.
FORBIDDEN_PRIMITIVES = (
    "subprocess",
    "os.system",
    "os.popen",
    "write_text",
    "json.dump",
    "open(",
)


def test_parse_boulder_current_fixture() -> None:
    """The current boulder shape yields the active campaign summary."""
    summary = parse_boulder_status(json.dumps(BOULDER_FIXTURE))
    assert summary.source == "boulder"
    assert summary.active_work == "lifeos-continuation-campaign-ac4ca534"
    assert summary.plan_name == "lifeos-continuation-campaign"
    assert summary.status == "active"
    assert summary.agent == "atlas"
    assert summary.started_at == "2026-09-16T05:16:14.069Z"
    assert summary.updated_at == "2026-09-16T06:29:05.600Z"
    assert summary.active_plan is not None
    assert summary.attempt_dir is None
    assert summary.pending_review is None
    assert summary.superseded_by is None
    assert summary.supersession_note is None


def test_parse_boulder_superseded_active_work() -> None:
    """A completed active work surfaces its supersession provenance."""
    fixture = copy.deepcopy(BOULDER_FIXTURE)
    works = fixture["works"]
    assert isinstance(works, dict)
    active = cast("dict[str, object]", works["lifeos-continuation-campaign-ac4ca534"])
    active["status"] = "completed"
    active["ended_at"] = "2026-09-16T07:00:00.000Z"
    active["superseded_by"] = "next-campaign-abc123"
    active["supersession_note"] = "Superseded by next-campaign"
    summary = parse_boulder_status(json.dumps(fixture))
    assert summary.status == "completed"
    assert summary.superseded_by == "next-campaign-abc123"
    assert summary.supersession_note == "Superseded by next-campaign"


def test_parse_boulder_current_task_extracted() -> None:
    """An in-flight task title is surfaced when the active work carries one."""
    fixture = copy.deepcopy(BOULDER_FIXTURE)
    works = fixture["works"]
    assert isinstance(works, dict)
    active = cast("dict[str, object]", works["lifeos-continuation-campaign-ac4ca534"])
    active["current_task"] = {
        "task_key": "todo:11",
        "task_title": "Hermes→OMO supervision contract",
    }
    summary = parse_boulder_status(json.dumps(fixture))
    assert summary.current_task == "Hermes→OMO supervision contract"


def test_parse_boulder_malformed_null_safe() -> None:
    """Malformed JSON yields a null-safe summary, never an exception."""
    summary = parse_boulder_status("{not json")
    assert summary.source == "boulder"
    assert summary.active_work is None
    assert summary.attempt_dir is None
    assert summary.pending_review is None
    assert summary.plan_name is None
    assert summary.status is None


def test_parse_boulder_non_object_null_safe() -> None:
    """Valid JSON that is not an object yields a null-safe summary."""
    summary = parse_boulder_status("[1, 2, 3]")
    assert summary.source == "boulder"
    assert summary.active_work is None
    assert summary.plan_name is None


def test_parse_ulw_loop_status() -> None:
    """The ulw-loop status shape yields attempt and review fields."""
    summary = parse_ulw_loop_status(json.dumps(ULW_LOOP_FIXTURE))
    assert summary.source == "ulw-loop"
    assert summary.active_work == "lifeos-continuation-campaign-ac4ca534"
    assert summary.attempt_dir == ".omo/run-continuation"
    assert summary.pending_review is True
    assert summary.plan_name == "lifeos-continuation-campaign"
    assert summary.status == "running"
    assert summary.started_at == "2026-09-16T05:16:14.069Z"


def test_parse_ulw_loop_malformed_null_safe() -> None:
    """Malformed ulw-loop JSON yields a null-safe summary."""
    summary = parse_ulw_loop_status("")
    assert summary.source == "ulw-loop"
    assert summary.active_work is None
    assert summary.attempt_dir is None
    assert summary.pending_review is None


def test_module_is_parse_only() -> None:
    """The supervisor module contains no subprocess or write primitives."""
    source = inspect.getsource(omo_supervisor)
    for token in FORBIDDEN_PRIMITIVES:
        assert token not in source
