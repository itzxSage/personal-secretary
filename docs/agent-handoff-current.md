# Agent Handoff — Current Session

## Current Mission

Deliver the first working end-to-end LifeOS vertical on a physical iPhone:
invoke LifeOS → speak "Plan every minute of my next seven days" → real Life Engine
plans the week → user approves → real Google Calendar updated.

## Definition of Done

Physical iPhone invocation → real Mac-hosted Life Engine → deterministic week plan →
proposal visible to user → approval → real Google Calendar events → verify on device.

## Wave 1 — Verified Foundation (Committed)

- **Week Planner backend** — `week_planning.py` (`WeekPlanningService` preview /
  approve-and-apply), `planner.py` / `planner_placement.py`, 10,080-minute validation.
- **iOS planning client** — `WeekPlanningContract.swift`, `LifeOSApprovalSigner`
  (byte-exact Python `Approval.signing_bytes` parity), `SignedConversationRelay`
  `previewWeekPlan()` / `approveWeekPlan(proposalID:approval:)`.
- **Verification** — backend `test_week_planning.py` / `test_week_planner.py` /
  `test_life_interview.py` / `test_life_knowledge.py`: 34 passed; iOS suite: 63 passed.

## Wave 2 — mTLS Week-Plan Relay Endpoints (Committed)

- **`relay_api.py`** now exposes:
  - `POST /v1/week-plan` (empty signed body) → `WeekPlanProposal`
  - `POST /v1/week-plan/{proposal_id}/approve` (body `{"approval": {...}}`) →
    `WeekPlanExecutionResult` (`state: applied`, `lease_id`, `applied_operations`)
  - `create_relay_app(open_store, clock, week_planning_factory=None,
    planning_timezone=...)` — factory seam; when `None` the relay **fails closed
    with 501** (the honest staging posture of `scripts/relay.py` serve mode,
    which documents "no provider or execution capabilities").
- **Error contract** (mapped from real service semantics via `.reason`):
  401 auth, 403 forged/wrong-device, 404 unknown proposal, 409 stale/replay or
  lease-rejected (proposal remains reviewable), 422 malformed/empty body,
  502 provider failure (NOT applied), 501 unconfigured factory.
- **`tests/test_relay_https.py`** — real loopback mTLS over uvicorn:
  - `serving(...)` contextmanager + slim `relay` fixture
  - `planning_relay` fixture: provisions device, seeds `person_routine`
    (subject_id = the device's PERSON, the subject-matching gotcha), sandbox
    `GoogleCalendarSandbox` via `make_adapter`, wired `WeekPlanningService`
    factory, `planning_timezone="UTC"`
  - `approval_for(...)` — reads the stored proposal, recomputes `payload_hash`,
    signs `signing_bytes("calendar.apply")` with the enrolled `SIGNING_KEY`
  - 6 new endpoint tests: preview mutation-free (FEASIBLE, 10,080 min,
    `mutation_count == 0`), approve applies once (+ replay is 409, not silent
    idempotency), foreign device 403, unknown proposal 404, unconfigured 501,
    (malformed-body 422 covered via existing conversation-path validation).
- **Verification** — focused relay + week_planning: 30 passed; full backend
  suite: **381 passed, 30 skipped** (skips are PostgreSQL-only).

## Important Architecture Invariants

- Life Engine owns canonical state; channels are subordinate infrastructure
- Proposal → approval → execution lease → external action (never bypassed)
- Payload-bound approvals, expiration, idempotency, audit, replay resistance
- KnowledgeState never silently promotes INFERRED → CONFIRMED
- Deterministic, explainable scheduling over globally optimal
- Writes only to the LifeOS **secondary** Google Calendar
- Physical acceptance: real Google Calendar events visible after approval

## Known Wire-Contract Notes

- FastAPI serializes computed fields (`PlanBlock.duration_minutes`) into the wire
  payload; the canonical model forbids them on input. Clients must tolerate unknown
  keys (the Swift decoder does). `tests/test_relay_https.py` mirrors this with
  `parse_wire_week_plan_proposal()` (strips the computed key before validating).

## Physical Device / User Gates (Wave 4)

- Apple signing / development team selection
- iPhone plugged/unlocked, developer certificate trust
- Microphone permission acceptance
- Google OAuth consent
- Tailscale connectivity verification

## Next Steps

- **Wave 3 (iOS UI/UX)** — extend `ConversationSession` with a week-plan proposal
  state machine (echo the preview `payload_hash` when approving, fresh
  `fact_id`/`issued_at`/`expires_at`+300s/`idempotency_key`/`actor`/
  `correlation_id`, `action_class = "calendar.apply"`) and add a **"Plan My Week"**
  button + proposal sheet to `ConversationShellView` using `AppBrand` tokens.
  `SignedConversationRelay` already has the two HTTP methods.
- **Wave 4** — full regression (backend `uv run pytest -q`, iOS
  `swift test --package-path ios/SecretaryApp`), then physical iPhone build/install
  and live Tailscale + Google Calendar verification.
- Resolve signing / network / permission issues as they arise on device.

## Start Here

Backend relay endpoints for week preview + approve are complete and tested over
real mTLS. iOS already has the signed client methods. The missing link is the
iOS UI: a "Plan My Week" affordance that calls `previewWeekPlan()`, presents the
proposal, and on user approval signs and sends it via `approveWeekPlan()`,
then shows the applied result.