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

## Wave 3 — Implemented Locally (Physical Acceptance Still Pending)

- `ConversationSession` owns idle/planning/proposed/applying/applied/recoverable-error
  state, retains the exact preview, and signs its proposal ID/hash with the enrolled
  key. Fresh approval IDs and a five-minute expiry; duplicate taps are blocked.
- Native Plan My Week sheet displays the real schedule, explanations, calendar
  projection, gaps, and dry-run consequences. Conflicts and empty changes cannot
  be approved. Uncertain apply responses never display success or auto-replay.
- The Xcode project was regenerated to include the previously missing client files
  and new app session tests. Signed physical iPhone build/install/launch succeeded.
- `relay.py serve --enable-week-planning --planning-timezone America/Chicago`
  explicitly wires the existing governed service to live Google Calendar. Default
  serve mode still fails closed for planning. The existing local LaunchAgent was
  updated; its previous plist is backed up beside it as `.plist.before-wave3`.
- Verification: Swift **73 passed**; full backend **391 passed, 30 skipped**; new
  Python files passed lint and type checks. All verified 2026-09-13.
- Live OAuth and the dedicated calendar verified. A signed mTLS preview against the
  running Mac returned HTTP 200. No external calendar write was attempted.

## Wave 3.5 — Bootstrap Import (Completed 2026-09-13)

- Private bootstrap files (`jared_lifeos_bootstrap_knowledge_v1.yaml`,
  `jared_lifeos_bootstrap_readme_v1.md`) located at `~/.config/lifeos/bootstrap/`.
  Protected by `.gitignore`; never staged or committed.
- `knowledge_import.py`: bounded provenance-bearing YAML ingestion into encrypted
  memory. Idempotent, transactional, rejects changed versions, skips tombstoned
  assertions. Every import assertion retains `KnowledgeSourceEvidence` (source
  provenance, SHA256, observation precision, original attributes) without asserting
  current truth.
- `import_life_knowledge.py`: CLI script importing against the live encrypted store.
- `week_planning_live.py`: explicit live Calendar wiring factory with process-local
  lease key.
- Interview infrastructure extended: `WEEK_PLANNING_TOPICS` (focused topic set),
  `InterviewProgress.objective` field (`"understanding"` / `"week_planning"`),
  `LifeInterview.begin(objective="week_planning")`, reconciliation mode for imported
  evidence, sensitivity gating for private domains.
- `KnowledgeDetails.source_evidence` and `.superseded_by` fields support evidence
  provenance and proper supersession without silent state promotion.
- **Import result**: 222 assertions ingested into the live store. States: 188 unknown,
  34 stale, 0 confirmed. Planning knowledge: **0 facts** (correct — no silent
  promotion).
- Interview verified working: `begin(objective="week_planning")` produces first
  question `permission.work` as expected.

## Current Gate — Week Planning Interview (Physical User Action Required)

**The import placed raw data into the store. The interview must reconcile it into
CONFIRMED facts before the week planner can generate a real proposal.**

The enrolled user must complete the week planning interview on the physical iPhone:

1. Open the SecretaryApp on the iPhone
2. Tap **"Know Me"** to start the interview
3. Answer each question — especially:
   - Work schedule for the next 7 days (dates, start/end times, timezone)
   - Fixed commitments missing from digital calendar
   - Community/spiritual commitments this week
   - Exercise routine (days, times, duration)
   - Sleep schedule (typical bedtime, wake time)
   - Meal times
4. The interview will review imported history and ask "What should I understand
   as true now?" — provide current answers to supersede stale imported data
5. When the interview completes, tap **"Plan My Week"**
6. Review the generated proposal
7. Approve if correct → check Google Calendar for real events

**What to expect**: The first preview may have few or zero calendar operations if
the interview hasn't yet produced enough CONFIRMED routine data. That is correct
behavior. Continue answering interview questions until enough deterministic inputs
exist.

**What to tell me afterward**: Whether the proposal showed a real schedule, whether
approval succeeded, and whether events appeared in Google Calendar.

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

## Physical Device / User Gates (Current)

- ✅ Apple signing / development team selection
- ✅ iPhone plugged/unlocked, developer certificate trust
- ✅ Microphone permission acceptance
- ✅ Google OAuth consent
- ✅ Tailscale connectivity (relay running on 192.168.12.133:8443)
- ⏳ **Week planning interview completion** — user must answer questions on iPhone
- ⏳ **Plan My Week approval** — user approves real proposal
- ⏳ **Google Calendar verification** — user confirms events appeared

## Next Steps

1. User completes week planning interview on physical iPhone (CURRENT GATE)
2. After enough CONFIRMED facts exist, tap "Plan My Week" to get a real proposal
3. Approve the exact proposal → verify Google Calendar events
4. Only after that acceptance succeeds, checkpoint and commit

## Phase 5 — Overnight Hardening (Completed 2026-09-13)

Commit: `ebb3a1b` (24 files, +2055/-57 lines)

### Security Review (Oracle, 10 min)

**0 CRITICAL, 0 HIGH, 2 MEDIUM, 6 LOW, several INFO.**

Authority boundaries hold — no path from the import to planning access, CONFIRMED
state, or code execution. The provenance/audit trail is content-free.

| # | Finding | Severity | Status |
|---|---------|----------|--------|
| 1 | Duplicate YAML keys silently last-wins (defeats human review of the file) | MEDIUM | **Fixed** — `_StrictSafeLoader` rejects duplicate keys |
| 2 | Tombstone bypass via new `--source-id` (documented upgrade path re-imports deleted facts) | MEDIUM | **Documented** — docstring warning added; full fix requires per-assertion content digest |
| 3 | Inline merge keys (`<<: {inline}`) bypass alias scan | LOW | Documented in findings |
| 4 | Metadata sub-dicts under claim nodes become assertions via `_nested_claims` | LOW | Documented in findings |
| 5 | Non-current imported claims stay relevant forever in interview | LOW | Documented in findings |
| 6 | Review prompt shows `relevant[:3]` but supersedes ALL evidence_ids | LOW | Documented in findings |
| 7 | OBSERVED claims retain source-provided `last_confirmed_at` | LOW | Documented in findings |
| 8 | dot-in-key vs nested dict → same path → IntegrityError (fail-closed) | LOW | Documented in findings |

Key design invariants verified:
- `planning_knowledge` requires PLANNING scope + `planning_allowed` + CONFIRMED/OBSERVED + confidence >= 0.8
- Only `correct_knowledge` with `allow_planning` grants planning access (explicit user command)
- CLI's `begin(week_planning)` only sets interview objective — no knowledge, no authority
- `MemoryRecord.validate_knowledge_source` blocks CONFIRMED + IMPORT provenance
- Signing key is process-local, never persisted, restart invalidates
- Leases are payload-bound, one-shot, expiring, require durable APPROVED proposals

### Privacy Check (Explore)

No private data leaks found in tracked files. Private bootstrap files at
`~/.config/lifeos/bootstrap/` remain gitignored.

### Test Results

- Backend: **391 passed, 30 skipped**
- iOS: **73 passed**
- Import tests: **11 passed** (9 original + 2 duplicate-key rejection parametrized cases)
- Ruff: clean on all new Python files
- Pyright: 0 errors on new files

### Phase 5C — Test Depth Gaps

28 gaps identified across the new code paths. Key findings by severity:

**CRITICAL (1):**
- Network interruption during apply leaves proposal stuck in `approved` with no recovery — `CalendarInterruptedError` propagates untyped from `WeekPlanningService.approve_and_apply` (only `CalendarAuthorizationError` and `CalendarContractError` are caught). The proposal was already transitioned to `approved`, so retries fail with `stale_proposal` and there is no reconcile/rollback path. Design gap, not just test gap.

**HIGH (6):**
- Reconciliation sensitivity escalation untested (`life_interview.py:398`) — RESTRICTED/SENSITIVE evidence answers narrowed to PRIVATE scope, but no test asserts this. Privacy-critical.
- No-enrolled-device authorization path untested (`week_planning.py:281`) — `DeviceNotFoundError` → `APPROVAL_REJECTED` never exercised.
- Malformed timestamp strings raise ungoverned `ValueError` (`knowledge_import.py:101`) — `datetime.fromisoformat` on bad strings not caught via `_reject`.
- Invalid confidence types untested (`knowledge_import.py:217`) — string, bool, out-of-range.
- `_atoms` depth limit untested (`knowledge_import.py:108`) — 22-level nesting.
- CLI script (`import_life_knowledge.py`) has zero test coverage.

**MEDIUM (14):** Schema rejections, empty/oversized sources, MAX_ASSERTIONS cap, YAML date normalization, list-of-lists recursion, empty/null values, metadata inheritance, _nested_claims list handling, UNKNOWN interview answers, multi-evidence reconciliation, non-reconciliation evidence path, KeychainCalendarIdStore errors, HTTPSGoogleHTTPClient interruption, restart test overclaims mechanism.

**LOW (7):** Summary counters, kind mappings, KEYS mappings, _content branches, year precision, interview guard rails, domain sensitivity.

**Design observations:**
- Interrupted apply has no recovery path (strongest follow-up candidate)
- Lease expiry is unreachable in service flow (issued and verified in same call)
- `{"value": None}` imports content `"None"` — needs a decision (reject or skip)
