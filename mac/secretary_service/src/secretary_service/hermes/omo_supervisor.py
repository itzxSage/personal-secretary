"""Parse-only Hermes supervision over OMO status output.

Hermes reads OMO state strictly as JSON text and never writes boulder or any
other ``.omo`` file. Supervision output feeds ephemeral context only; it is
never persisted by Hermes. See ``docs/hermes-omo-contract.md`` for the full
read-only contract.
"""

import json
from dataclasses import dataclass
from typing import cast


@dataclass(frozen=True)
class OmoStatusSummary:
    """Read-only snapshot of OMO supervision state.

    Every field is optional: a missing or malformed status document yields a
    summary with ``None`` fields rather than raising. ``source`` names the
    status command that produced the document (``"boulder"`` or
    ``"ulw-loop"``).
    """

    source: str
    active_work: str | None = None
    attempt_dir: str | None = None
    pending_review: bool | None = None
    plan_name: str | None = None
    status: str | None = None
    started_at: str | None = None
    updated_at: str | None = None
    agent: str | None = None
    active_plan: str | None = None
    superseded_by: str | None = None
    supersession_note: str | None = None
    current_task: str | None = None


def _text(value: object, key: str) -> str | None:
    """Return ``value[key]`` when it is a string, else ``None``."""
    if isinstance(value, dict):
        raw = cast("dict[str, object]", value).get(key)
        return raw if isinstance(raw, str) else None
    return None


def _flag(value: object, key: str) -> bool | None:
    """Return ``value[key]`` when it is a boolean, else ``None``."""
    if isinstance(value, dict):
        raw = cast("dict[str, object]", value).get(key)
        return raw if isinstance(raw, bool) else None
    return None


def _load_mapping(json_text: str) -> dict[str, object] | None:
    """Parse JSON text into a mapping, or None when malformed/non-object."""
    try:
        parsed = cast("object", json.loads(json_text))
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(parsed, dict):
        return cast("dict[str, object]", parsed)
    return None


def _active_entry(data: dict[str, object]) -> dict[str, object] | None:
    """Return the work referenced by ``active_work_id``, else the active one.

    The ``active_work_id`` entry is authoritative even when its status is
    ``completed`` (a superseded work remains the referenced active work until a
    successor starts). The ``status == "active"`` scan is the fallback for
    documents that omit ``active_work_id``.
    """
    works = data.get("works")
    if not isinstance(works, dict):
        return None
    typed_works = cast("dict[str, object]", works)
    active_work_id = _text(data, "active_work_id")
    if active_work_id is not None:
        entry = typed_works.get(active_work_id)
        if isinstance(entry, dict):
            return cast("dict[str, object]", entry)
    for entry in typed_works.values():
        if _text(entry, "status") == "active" and isinstance(entry, dict):
            return cast("dict[str, object]", entry)
    return None


def parse_boulder_status(json_text: str) -> OmoStatusSummary:
    """Parse ``omo boulder --json`` output into a null-safe summary.

    The active work is taken from the top-level ``active_work_id`` (falling
    back to the ``status == "active"`` entry in ``works``). Mirror fields
    prefer the active work entry and fall back to the top-level legacy fields.
    """
    data = _load_mapping(json_text)
    if data is None:
        return OmoStatusSummary(source="boulder")
    active_entry = _active_entry(data)

    def _pick(entry_key: str, top_key: str) -> str | None:
        if active_entry is not None:
            value = _text(active_entry, entry_key)
            if value is not None:
                return value
        return _text(data, top_key)

    active_work = _text(data, "active_work_id")
    if active_work is None and active_entry is not None:
        active_work = _text(active_entry, "work_id")
    current_task: str | None = None
    if active_entry is not None:
        task = active_entry.get("current_task")
        if isinstance(task, dict):
            current_task = _text(cast("dict[str, object]", task), "task_title")
    return OmoStatusSummary(
        source="boulder",
        active_work=active_work,
        plan_name=_pick("plan_name", "plan_name"),
        status=_pick("status", "status"),
        started_at=_pick("started_at", "started_at"),
        updated_at=_pick("updated_at", "updated_at"),
        agent=_pick("agent", "agent"),
        active_plan=_pick("active_plan", "active_plan"),
        superseded_by=_text(active_entry, "superseded_by"),
        supersession_note=_text(active_entry, "supersession_note"),
        current_task=current_task,
    )


def parse_ulw_loop_status(json_text: str) -> OmoStatusSummary:
    """Parse ``omo ulw-loop status --json`` output into a null-safe summary.

    The active work is taken from ``work_id`` when present, else ``plan``.
    """
    data = _load_mapping(json_text)
    if data is None:
        return OmoStatusSummary(source="ulw-loop")
    active_work = _text(data, "work_id")
    if active_work is None:
        active_work = _text(data, "plan")
    return OmoStatusSummary(
        source="ulw-loop",
        active_work=active_work,
        attempt_dir=_text(data, "attempt_dir"),
        pending_review=_flag(data, "pending_review"),
        plan_name=_text(data, "plan"),
        status=_text(data, "status"),
        started_at=_text(data, "started_at"),
        updated_at=_text(data, "updated_at"),
    )
