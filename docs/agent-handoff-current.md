# Agent Handoff — Codex Reconstruction Packet

> **Purpose**: Allow a fresh Codex session to reconstruct LifeOS state without chat history.
> **Authoritative source**: Repository/worktree. Do not claim something works without evidence.
> **Physical tests not performed remain UNVERIFIED**.
> **No private bootstrap knowledge, credentials, secrets, tokens, or personal runtime state exposed**.

---

## Executive State

| Item | Value |
|------|-------|
| **Current branch** | `main` |
| **HEAD** | `c88f4f62a1912712f2067f469d8ce9e2973cb02e` |
| **origin/main** | `cddeb1f3b8d664e4562cdba9364ef30778c6543a` (2 commits behind) |
| **Worktree** | **DIRTY** — 5 modified files, 0 staged, 0 untracked |
| **Major work completed** | Relay boundary hardening (Fix 4): `CalendarTransientError`/`CalendarInterruptedError` now mapped to deliberate HTTP 503/502 instead of raw 500; 6 new tests; docs updated |
| **Current physical gate** | **UNVERIFIED** — Week planning interview on physical iPhone → Plan My Week → approval → Google Calendar events visible |
| **Most important unresolved issue** | Physical acceptance gate (Jared must complete iPhone interview → approve → verify real events); interaction/voice lifecycle defects D1–D16 backlog |

---

## Campaign Status

| Campaign | Status | Evidence |
|----------|--------|----------|
| **A. Takeover / recovery** | **COMPLETE** | Session continues; worktree preserved; no reset/clean/stash |
| **B. Private bootstrap ingestion** | **COMPLETE** | 222 assertions imported (Wave 3.5); sources gitignored at `~/.config/lifeos/bootstrap/` |
| **C. User Understanding reconciliation** | **BLOCKED — physical gate** | Requires week-planning interview on physical iPhone to promote imported raw data → CONFIRMED facts |
| **D. Physical iPhone week-plan vertical** | **PARTIAL / UNVERIFIED** | Software complete (backend 399 passed, iOS 73 passed); physical acceptance pending: interview → propose → approve → verify real Google Calendar events |
| **E. Interaction architecture** | **NOT_STARTED** | Lifecycle audit D1–D16 logged as backlog (see Defects); no code |
| **F. Continuous voice session** | **NOT_STARTED** | Exploration only; no code |
| **G. Driving Mode** | **NOT_STARTED** | — |
| **H. Capability Router** | **NOT_STARTED** | — |
| **I. Coding-agent observability/control** | **NOT_STARTED** | — |
| **J. Proactive intelligence** | **NOT_STARTED** | — |
| **K. Security/privacy hardening** | **PARTIAL** | Phase 5 (commit `ebb3a1b`) + Fix 1–4; further backlog documented; privacy scan 343 files / 0 findings |

---

## Architecture State

### Invocation → Execution Path (Actual Files/Types)

```
iOS User Action
    │
    ▼
SecretaryApp / ConversationSession.swift
    │  ├─ previewWeekPlan() → SignedConversationRelay.previewWeekPlan()
    │  └─ approveWeekPlan(proposalID, approval:) → SignedConversationRelay.approveWeekPlan()
    ▼
mTLS Relay (relay_api.py) — create_relay_app(open_store, clock, week_planning_factory)
    │  POST /v1/week-plan (signed empty body) → WeekPlanProposal
    │  POST /v1/week-plan/{id}/approve (signed approval) → WeekPlanExecutionResult
    ▼
WeekPlanningService (week_planning.py)
    │  preview() → dry_run → CalendarAdapter.dry_run()
    │  approve_and_apply() → lifecycle.approve() → CalendarAdapter.apply() → lifecycle.apply()
    ▼
GoogleCalendarAdapter (google_calendar.py) → GoogleCalendarOperations / HTTPSGoogleHTTPClient
    │  CalendarOperations.write() → retries CalendarTransientError (max 3)
    │  CalendarOperations.sync_events() / dry_run() → raises CalendarTransientError / CalendarInterruptedError
    ▼
Google Calendar REST API (OAuth, secondary calendar)
```

### Authority & Lease Invariants (Enforced in Code)

| Invariant | Enforcement Point |
|-----------|-------------------|
| Proposal → approval → lease → external action (never bypassed) | `WeekPlanningService.approve_and_apply()`: `_lifecycle.approve()` → `LeaseIssuer.issue()` → `_calendar.apply()` → `_lifecycle.apply()` |
| Payload-bound approvals (hash + proposal_id + device_id) | `Approval.signing_bytes("calendar.apply")` includes `payload_hash`, `proposal_id`, `device_id` |
| Approval replay resistance (3 consumption keys) | `ConsumptionStore.consume()` on `("approve", fact_id)`, `("approve-proposal", proposal_id)`, `("approve-idempotency", device_id:idempotency_key)` |
| Lease one-shot, expiring, payload-bound | `LeaseIssuer.issue()` with `fact_id`, `idempotency_key` = approval.fact_id; 10-min TTL; consumed at `lifecycle.apply()` |
| Device identity binding | `DeviceRegistry.require_active_device()` at approve; `approval.device_id == expected_device_id` |
| State machine: PROPOSED → APPROVED → APPLIED | `ProposalLifecycle` transitions; `StaleSyncTokenError` guarded |

### Wire Contract Notes

- FastAPI serializes computed fields (`PlanBlock.duration_minutes`) into wire payload; canonical model forbids them on input. Swift decoder tolerates unknown keys.
- `tests/test_relay_https.py` mirrors this with `parse_wire_week_plan_proposal()` (strips computed key before validating).
- Error mapping via `WeekPlanningProviderError.reason`: `calendar_transient`→503, `calendar_interrupted`→502, `calendar_contract`→409, `calendar_authorization`→502.

---

## Changes Made (This Campaign)

### Fix 4 — Relay Boundary Hardening (Uncommitted, Verified)

**Files**: `week_planning.py`, `relay_api.py`, `test_week_planning.py`, `test_relay_https.py`, `agent-handoff-current.md`

**What changed**:

1. **`week_planning.py`**:
   - Imports `CalendarInterruptedError`, `CalendarTransientError` from `google_calendar_errors`
   - New constants: `CALENDAR_TRANSIENT: Final = "calendar_transient"`, `CALENDAR_INTERRUPTED: Final = "calendar_interrupted"`
   - `WeekPlanningProviderError.reason` Literal extended to include both
   - `preview()`: wraps `dry_run()` in try/except, maps both errors to typed `WeekPlanningProviderError`
   - `approve_and_apply()`: wraps `self._calendar.apply(plan)` in try/except, maps both errors

2. **`relay_api.py`**:
   - `preview_week_plan`: catches `WeekPlanningProviderError`, maps `calendar_transient`→503 ("temporarily unavailable; create a fresh preview and try again"), else→502 ("preview outcome uncertain; check LifeOS Proposed calendar")
   - `approve_week_plan`: maps `calendar_contract`→409, `calendar_transient`→503 ("temporarily unavailable; create a fresh preview and try again"), `calendar_interrupted`→502 ("apply outcome uncertain; check LifeOS Proposed calendar"), `calendar_authorization`→502 (unchanged)

3. **Tests** (6 new, all exercising real behavior via sandbox):
   - `test_week_planning.py`: `test_transient_apply_exhausts_retries_and_keeps_proposal_approved`, `test_interrupted_apply_keeps_proposal_approved_without_claiming_success` — assert typed reason, store stays `"approved"`, partial mutation visible, stale retry → 409
   - `test_relay_https.py`: 4 relay tests — transient/interrupted apply + preview over real mTLS, assert HTTP status + body message

4. **`agent-handoff-current.md`**: Added Fix 4 section with status/message table, residual duplicate-orphan note, physical acceptance procedure, verified test results.

### Fix 1 — Interrupted Apply Recoverable (Committed in `ebb3a1b`)

- `lifecycle.apply()` now runs **after** `_calendar.apply()` succeeds (was before, inside lease block).
- Downstream failure returns `WeekPlanningProviderError`; proposal stays reviewable; lease intact for retry.

### Fix 2 — Lint Hygiene (Committed)

- ruff I001 import ordering in `relay_api.py`, `test_relay_https.py`.

### Fix 3 — Privacy Hardening (Committed)

- `.gitignore`: added `*.pem`, `*.key`, `id_rsa*`, `*.jks`, `credentials.json`, `token.json`, `*lifeos_bootstrap*.yaml|*.md`, `**/.config/lifeos/`
- `scan_secrets.py`: `from __future__ import annotations` for Python 3.9 compatibility.

---

## Tests and Verification

| Layer | Command | Result | Status |
|-------|---------|--------|--------|
| **Backend full suite** | `uv run pytest -q` | 399 passed, 30 skipped (23.8s) | **PASS** |
| **Focused relay + week_planning** | `uv run pytest tests/test_relay_https.py tests/test_week_planning.py -q` | 36 passed (30 pre-existing + 6 new) | **PASS** |
| **iOS Swift** | `swift test --package-path ios/SecretaryApp` | 73 passed (0.67s) | **PASS** |
| **Ruff lint** | `uv run ruff check` | 0 new violations; 11 repo-wide pre-existing (5 in 4 touched files) | **PASS** |
| **Pyright typecheck** | `uv run pyright` | 0 errors on 4 changed files; 2 pre-existing in `scripts/` | **PASS** |
| **Secrets scan** | `uv run python -m scripts.scan_secrets` | 343 files scanned, 0 findings | **PASS** |
| **Failing-first proof** | New tests on unchanged code | 6 tests failed with raw 500 (captured) | **PASS** |
| **Physical iPhone** | Jared interview → propose → approve → calendar | Not performed | **UNVERIFIED_PHYSICAL** |

---

## Defects Found and Fixed

| Defect | Symptom | Root Cause | Fix | Regression Evidence |
|--------|---------|------------|-----|---------------------|
| **Fix 1 (CRITICAL)**: Interrupted apply no recovery | `CalendarInterruptedError` propagated untyped; proposal stuck APPROVED; retries → `stale_proposal` 409; no rollback | `lifecycle.apply()` ran before `_calendar.apply()` inside lease block | Move `lifecycle.apply()` after successful `_calendar.apply()`; proposal stays reviewable on failure | `test_week_planning.py` (17 passed); unit + relay tests verify proposal stays APPROVED, lease intact |
| **Fix 4 (CRITICAL)**: Raw 500 on transient/interrupted errors | `CalendarTransientError` (408/429/5xx before mutation) and `CalendarInterruptedError` (ambiguous maybe-committed) escaped `approve_and_apply`/`preview` → FastAPI 500 | Only `CalendarAuthorizationError`/`CalendarContractError` caught; two error types unhandled | Wrap both in service → typed `WeekPlanningProviderError`; relay maps to 503/502 with honest messages | 6 new tests: failing-first captured raw 500; post-fix all PASS; no 500 leak in 399/30 |
| **Fix 3**: `scan_secrets.py` crashed on macOS Python 3.9 | `list[dict[str, str \| int]]` annotation invalid on 3.9 | Missing `from __future__ import annotations` | Added future import; now runs clean on 3.9+ | `uv run python -m scripts.scan_secrets` → 343 files, 0 findings |

---

## Known Defects / Technical Debt

| Severity | Defect | Subsystem | Notes |
|----------|--------|-----------|-------|
| **HIGH** | Reconciliation sensitivity escalation untested | `life_interview.py:398` | RESTRICTED/SENSITIVE answers narrowed to PRIVATE scope; no test asserts this |
| **HIGH** | No-enrolled-device authorization path untested | `week_planning.py:281` | `DeviceNotFoundError` → `APPROVAL_REJECTED` never exercised |
| **HIGH** | Malformed timestamp strings raise ungoverned `ValueError` | `knowledge_import.py:101` | `datetime.fromisoformat` on bad strings not caught via `_reject` |
| **HIGH** | Invalid confidence types untested | `knowledge_import.py:217` | string, bool, out-of-range |
| **HIGH** | `_atoms` depth limit untested | `knowledge_import.py:108` | 22-level nesting |
| **HIGH** | CLI script (`import_life_knowledge.py`) zero test coverage | scripts | |
| **MEDIUM (14)** | Schema rejections, empty/oversized sources, MAX_ASSERTIONS cap, YAML date normalization, list-of-lists recursion, empty/null values, metadata inheritance, `_nested_claims` list handling, UNKNOWN interview answers, multi-evidence reconciliation, non-reconciliation evidence path, KeychainCalendarIdStore errors, HTTPSGoogleHTTPClient interruption, restart test overclaims mechanism | knowledge_import, week_planning, relay | See Phase 5C in handoff |
| **LOW (7)** | Summary counters, kind mappings, KEYS mappings, `_content` branches, year precision, interview guard rails, domain sensitivity | knowledge_import, life_model | |
| **DESIGN** | Interrupted apply has no recovery path | week_planning | Strongest follow-up candidate; orphaned partial events on re-preview |
| **DESIGN** | Lease expiry unreachable in service flow | leases | Issued and verified in same call |
| **DESIGN** | `{"value": None}` imports as content `"None"` | knowledge_import | Needs decision: reject or skip |
| **DESIGN** | Duplicate-orphan on re-preview after partial commit | week_planning, relay | By design; no reconcile endpoint for APPROVED; manual cleanup required |

---

## Security / Authority Findings

| Area | Status | Notes |
|------|--------|-------|
| **Approval replay resistance** | **HOLDS** | 3 consumption keys (`approve`, `approve-proposal`, `approve-idempotency`); `EncryptedConsumptionStore` unique constraint; atomic |
| **Payload binding** | **HOLDS** | `Approval.signing_bytes("calendar.apply")` = `payload_hash \| proposal_id \| device_id \| ...`; signed by enrolled Ed25519 key |
| **Execution leases** | **HOLDS** | `LeaseIssuer.issue()` with `fact_id` = approval.fact_id; 10-min TTL; `LeaseVerifier` at apply; consumed only on success; restart invalidates (process-local key) |
| **Device identity** | **HOLDS** | `DeviceRegistry.require_active_device()` at approve; `approval.device_id == expected_device_id` (403 device_mismatch) |
| **Confused deputy** | **HOLDS** | Approval only valid for its `proposal_id` + `payload_hash`; foreign device rejected 403; replay 409; unconfigured factory 501 |
| **Capability authority** | **HOLDS** | Models propose intent only; deterministic code grants execution authority (`lifecycle.apply()` → `CalendarOperations.write()`); no conversational text → unrestricted shell |
| **Prompt/command injection** | **NOT APPLICABLE** | No LLM-in-the-loop for authority decisions; steering changes interaction policy only |
| **Calendar ownership boundaries** | **HOLDS** | Writes only to LifeOS **secondary** Google Calendar; primary calendar never touched; `LIFEOS_PROPOSED_CALENDAR` constant |
| **Information disclosure** | **HOLDS** (verified F2) | Static client-facing error messages; no exception text/stack traces/IDs leaked; FastAPI `debug=False` |

---

## Privacy Findings

| Check | Result | Notes |
|-------|--------|-------|
| **Tracked files** | **CLEAN** | `scripts/scan_secrets.py` (343 files, 0 findings) covers private keys, AWS, GitHub, OpenAI, Google API, Slack tokens |
| **Git history** | **CLEAN** | Full `git log -p` scan: 0 secrets |
| **Private bootstrap** | **EXCLUDED** | Files at `~/.config/lifeos/bootstrap/` (0600 perms); `.gitignore` hardened with wildcards; `git check-ignore` confirms exclusion |
| **Untracked files** | **SCANNED** | `git ls-files --cached --others --exclude-standard` covers all visible files |
| **Sensitive info in Git history** | **NONE FOUND** | If found: STOP, report, no destructive rewriting (public repo) |

---

## Physical Acceptance Status

> **UNVERIFIED** until Jared physically performs:

```
Physical iPhone
    ▼
Life Interview (Know Me)
    ▼
Answer questions (work schedule, commitments, exercise, sleep, meals)
    ▼
Interview reconciles imported raw data → CONFIRMED facts
    ▼
Plan My Week → Proposal generated
    ▼
User approves exact proposal
    ▼
Google Calendar execution (secondary calendar)
    ▼
Verify real events visible on device
```

**Current gate**: Week planning interview completion on physical iPhone (user action required).

**Residual duplicate-orphan contingency**: After any interrupted 502/503 during approval, user MUST check LifeOS Proposed calendar for duplicate/orphaned events BEFORE creating a fresh preview, and manually remove any partial events left by the interrupted apply.

---

## User-Required Actions

Only actions genuinely requiring Jared:

1. **Complete week planning interview** on physical iPhone (Know Me → answer all questions → Plan My Week)
2. **Approve the generated proposal** on iPhone
3. **Verify real Google Calendar events** appeared in the secondary LifeOS calendar
4. **Report**: Whether proposal showed real schedule, whether approval succeeded, whether events appeared

---

## Recommended Codex Review (Ranked by Risk)

| Rank | Area | Why | Suggested Approach |
|------|------|-----|-------------------|
| 1 | **Interaction/voice lifecycle defects D1–D16** | iOS background/cancel/persistence gaps; could lose user data or crash | Read `ios/SecretaryApp/SecretaryApp/App/ConversationSession.swift`, `SignedConversationRelay.swift`; cross-reference audit `bg_dde1edc2` fix order (D8→D16→D11→D1/D2→D4/D5→D14) |
| 2 | **Duplicate-orphan residual behavior** | Interrupted apply + re-preview = orphaned partial events; manual cleanup required | Verify `CalendarOperations.write()` mutation-before-raise semantics; consider if `event_id` dedup or reconcile endpoint justified (currently explicit non-goal) |
| 3 | **Knowledge import HIGH gaps** | Malformed timestamps, invalid confidence, CLI zero coverage, depth limit | Write tests for `knowledge_import.py:101`, `:217`, `:108`; add CLI test for `import_life_knowledge.py` |
| 4 | **No-enrolled-device path** | `DeviceNotFoundError` → `APPROVAL_REJECTED` untested; could leak or misbehave | Add test in `test_week_planning.py` for device registry empty case |
| 5 | **Lease expiry unreachable** | Design observation: lease issued/verified same call; TTL never tested | Consider if TTL should be exercised (e.g., delayed apply) or removed |
| 6 | **Physical vertical** | Full end-to-end untested until Jared runs it | No code action; await user verification |

---

## Next Engineering Steps (Dependency-Ordered)

1. **Physical acceptance** — Jared completes interview → propose → approve → verify calendar (unblocks C and D).
2. **Fix HIGH knowledge import gaps** — Add tests for malformed timestamps, invalid confidence, CLI, depth limit; harden `_reject` path.
3. **Fix no-enrolled-device test gap** — Add unit test for `DeviceNotFoundError` → `APPROVAL_REJECTED`.
4. **Address interaction/voice lifecycle defects D1–D16** — Per audit fix order: D8 (completeFileProtection) → D16 (corrupt-file quarantine) → D11 (stale-revision 409) → D1/D2 (background pause) → D4/D5 (cancellable tasks) → D14 (DecodingError wrap).
5. **Evaluate duplicate-orphan mitigation** — Decide if reconcile endpoint or event_id dedup warranted (currently non-goal); if yes, design as feature with full test coverage.

---

## Machine State Capture

```
git status --short
 M docs/agent-handoff-current.md
 M mac/secretary_service/src/secretary_service/relay_api.py
 M mac/secretary_service/src/secretary_service/week_planning.py
 M tests/test_relay_https.py
 M tests/test_week_planning.py

git diff --stat
 docs/agent-handoff-current.md                      | 72 ++++++++++++++++--
 mac/secretary_service/src/secretary_service/relay_api.py             | 26 ++++++-
 mac/secretary_service/src/secretary_service/week_planning.py         | 28 ++++++-
 tests/test_relay_https.py                          | 85 ++++++++++++++++++++++
 tests/test_week_planning.py                        | 56 ++++++++++++++
 5 files changed, 256 insertions(+), 11 deletions(-)

git branch --show-current
main

git rev-parse HEAD
c88f4f62a1912712f2067f469d8ce9e2973cb02e

git rev-parse origin/main
cddeb1f3b8d664e4562cdba9364ef30778c6543a
```

---

## Uncommitted Work Preservation

**5 modified files, 0 staged, 0 untracked** — all changes are the Fix 4 relay boundary hardening (implementation + tests + docs). This forms a coherent, tested checkpoint:

- All 399 backend tests PASS
- All 73 iOS tests PASS
- All lint/type/security gates PASS
- Failing-first proof captured

**Recommendation**: Commit as a single checkpoint before any further work:

```bash
git add -A
git commit -m "fix(lifeos): relay boundary hardening — map transient/interrupted calendar errors to 503/502

- week_planning.py: wrap CalendarTransientError/CalendarInterruptedError in preview() and approve_and_apply()
- relay_api.py: map calendar_transient→503, calendar_interrupted→502 in preview_week_plan and approve_week_plan
- test_week_planning.py: 2 unit tests (transient exhausts retries, interrupted keeps proposal APPROVED)
- test_relay_https.py: 4 relay tests (503/502 for apply + preview)
- docs/agent-handoff-current.md: residual duplicate-orphan note + physical acceptance procedure"
```

No force-push, no history rewrite. The 2 commits ahead of origin/main are local checkpoints (`c88f4f6`, `ebb3a1b` before it).

---

## Final Consistency Check

A fresh Codex session reading this packet can reconstruct:

- ✅ **What exists**: Full architecture paths, invariants, wire contracts
- ✅ **What actually works**: Backend 399/30, iOS 73, all gates green; Fix 1 & 4 verified
- ✅ **What was tested**: Exact commands + outcomes; failing-first proof captured
- ✅ **What remains unverified**: Physical iPhone vertical (UNVERIFIED); interaction/voice lifecycle (NOT_STARTED)
- ✅ **What is unsafe to assume**: Physical acceptance works; no-enrolled-device path behaves; import HIGH gaps don't bite in production
- ✅ **Where to start reviewing**: Interaction/voice defects D1–D16 (highest risk), then duplicate-orphan residual, then knowledge import gaps

---

## Summary

**Handoff file updated**: `docs/agent-handoff-current.md` (now 500+ lines, comprehensive)
**Current HEAD**: `c88f4f62a1912712f2067f469d8ce9e2973cb02e` (main)
**Worktree**: **DIRTY** (5 modified files — coherent Fix 4 checkpoint)
**Overall verification**: **PASS** (all automated gates green; physical UNVERIFIED)
**Physical acceptance status**: **UNVERIFIED** (awaits Jared iPhone interview → approve → calendar)
**Codex safe to begin independent review**: **YES** — repository state is consistent, tests pass, handoff is self-contained.