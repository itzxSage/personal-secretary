# Hermes → OMO Supervision Contract

> Status: **Read-only status integration — implemented.** Hermes observes OMO
> campaign state strictly as JSON text and never writes boulder or any other
> `.omo` file. Supervision output feeds ephemeral context only.

## 1. Purpose

Hermes (the pinned LifeOS sidecar, `v2026.9.7` /
`2237be355906fbe6065ce1815711eee52b2d646e`) needs a bounded view of the OMO
campaign it is embedded in: which work is active, where the continuation
attempt lives, and whether a review is pending. This contract defines how that
view is obtained **without granting Hermes any authority over OMO state**.

The companion implementation is
`secretary_service/hermes/omo_supervisor.py` — a **parse-only** helper. It
never executes `omo`, never spawns subprocesses, and never writes to
`.omo/boulder.json` or any other `.omo` file.

## 2. The Contract

1. **Hermes must NEVER write boulder directly.** No `omo boulder` mutation, no
   direct edit of `.omo/boulder.json`, no write to `.omo/plans/`,
   `.omo/notepads/`, `.omo/evidence/`, or `.omo/run-continuation/`. OMO owns
   its state; Hermes only reads it.
2. **Supervision output feeds ephemeral context only.** Status summaries are
   injected into a single conversation turn's context and are never persisted
   by Hermes, never written to disk, and never used as a source of authority
   for actions.
3. **Read-only status integration.** Hermes consumes the output of:
   - `omo boulder --json` → parsed by `parse_boulder_status()`
   - `omo ulw-loop status --json` → parsed by `parse_ulw_loop_status()`
   Both return an `OmoStatusSummary` with the active work, attempt directory,
   pending-review flag, and raw mirror fields.
4. **Null-safe by design.** Missing, malformed, or non-object JSON yields a
   summary with `None` fields — never an exception, never a partial write.
5. **No authority expansion.** `hermes --yolo` (PID 86508) remains running and
   bounded. This contract adds observation only; it grants no new capabilities,
   no steering, and no write path.

## 3. Status Summary Shape

`OmoStatusSummary` (frozen dataclass) carries:

| Field | Source | Meaning |
|---|---|---|
| `source` | both | `"boulder"` or `"ulw-loop"` |
| `active_work` | boulder `active_work_id` / ulw-loop `work_id` or `plan` | Current work identifier |
| `attempt_dir` | ulw-loop `attempt_dir` | Continuation attempt directory (boulder: `None`) |
| `pending_review` | ulw-loop `pending_review` | Whether a review is pending (boulder: `None`) |
| `plan_name` | boulder `plan_name` / ulw-loop `plan` | Plan slug |
| `status` | boulder `status` / ulw-loop `status` | `active` / `completed` / `paused` / `abandoned` |
| `started_at` / `updated_at` | both | ISO-8601 timestamps |
| `agent` | boulder `agent` | Orchestrating agent (e.g. `atlas`) |
| `active_plan` | boulder `active_plan` | Absolute plan path |
| `superseded_by` / `supersession_note` | boulder active work entry | Supersession provenance when present |
| `current_task` | boulder active work `current_task.task_title` | In-flight task title when present |

## 4. Boulder JSON Shape (schema_version 2)

`omo boulder --json` mirrors `.omo/boulder.json`:

```jsonc
{
  "schema_version": 2,
  "active_work_id": "lifeos-continuation-campaign-ac4ca534",
  "works": {
    "lifeos-continuation-campaign-ac4ca534": {
      "work_id": "lifeos-continuation-campaign-ac4ca534",
      "plan_name": "lifeos-continuation-campaign",
      "status": "active",
      "started_at": "2026-09-16T05:16:14.069Z",
      "updated_at": "2026-09-16T06:29:05.600Z",
      "agent": "atlas"
    }
  },
  // Legacy mirror fields of the active work:
  "active_plan": "/Users/.../.omo/plans/lifeos-continuation-campaign.md",
  "plan_name": "lifeos-continuation-campaign",
  "status": "active",
  "started_at": "2026-09-16T05:16:14.069Z",
  "updated_at": "2026-09-16T06:29:05.600Z",
  "agent": "atlas"
}
```

Completed works carry `ended_at` and may carry `superseded_by` plus a
`supersession_note` (e.g. the legacy `lifeos-master-plan-59ad3eeb` entry).

## 5. ulw-loop Status Shape (assumed)

`omo ulw-loop status --json` is parsed tolerantly against this shape; unknown
or missing keys degrade to `None`:

```jsonc
{
  "active": true,
  "plan": "lifeos-continuation-campaign",
  "work_id": "lifeos-continuation-campaign-ac4ca534",
  "attempt_dir": ".omo/run-continuation",
  "attempt": "ses_f57870431ffe56uqAEwG8hWF9u",
  "pending_review": true,
  "session_id": "opencode:ses_f57870431ffe56uqAEwG8hWF9u",
  "status": "running",
  "started_at": "2026-09-16T05:16:14.069Z",
  "updated_at": "2026-09-16T06:29:05.600Z"
}
```

## 6. Enforcement

- The module imports only `json`, `dataclasses`, and `typing` — no
  `subprocess`, no `os`, no file-writing primitives.
- A regression test scans the module source and asserts the absence of
  subprocess/write primitives (`subprocess`, `os.system`, `os.popen`,
  `write_text`, `json.dump`, `open(`).
- `parse_boulder_status` / `parse_ulw_loop_status` are pure functions: same
  input, same output, no side effects.

## 7. Current Campaign Snapshot (2026-09-16)

- Active work: `lifeos-continuation-campaign-ac4ca534` (`status: active`,
  agent `atlas`, started `2026-09-16T05:16:14.069Z`).
- Legacy work: `lifeos-master-plan-59ad3eeb` (`status: completed`, ended
  `2026-09-16T05:50:00.000Z`, superseded by the continuation campaign).
- `hermes --yolo` (PID 86508) remains running/bounded — no authority expansion
  in this campaign.