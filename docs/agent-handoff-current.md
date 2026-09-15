# LifeOS — Independent Review Checkpoint

Updated by Codex, 2026-09-14. Repository and fresh test results are authoritative.
**Software acceptance status: NOT_READY. Physical acceptance: UNVERIFIED.**
This is an engineering review checkpoint. The complete takeover review remains in progress.

## Repository reconstruction and publication

- Started on `main` at `c88f4f62a1912712f2067f469d8ce9e2973cb02e`.
- Initial `origin/main`: `cddeb1f3b8d664e4562cdba9364ef30778c6543a`.
- Two local commits ahead: `987fb49` and `c88f4f6`. Earlier references to
  `ebb3a1b` do not describe this branch's current commit sequence.
- Initially five unstaged files: this handoff, `relay_api.py`, `week_planning.py`,
  `test_relay_https.py`, and `test_week_planning.py`; no staged or untracked files.
- Read both handoffs and inspected status, complete diffs, cached diff, history,
  branch and local/remote commit IDs. No work was reset, discarded, or stashed.
- At Jared's request, committed the inherited changes as `59cef8a` and pushed
  `main` to `git@github.com:itzxSage/personal-secretary.git`. Confirmed the remote
  branch matched the local commit. Subsequent review repairs are a separate checkpoint.
- Ignored credentials, private bootstrap sources, databases and build artifacts
  are not part of the GitHub source repository.

## Independently verified

### Primary vertical and its missing connection

The implemented path is:

1. iPhone **Know Me** calls `SignedConversationRelay.startInterview()`.
2. Signed mTLS relay derives the subject from the enrolled device's participant.
3. `LifeInterview` saves prose answers as current user statements/corrections;
   imported source evidence is retained separately when reconciled.
4. **Plan My Week** invokes `WeekPlanningService.preview()`.
5. `LifeModel.planning_knowledge()` admits only current, sufficiently confident,
   confirmed/observed records with both planning scope and `planning_allowed`.
6. `WeekPlanningService` converts `RoutineDetails` into deterministic activities,
   builds a 10,080-minute proposal and performs a read-only Calendar preview.
7. Explicit iPhone approval signs the stored proposal ID and payload hash.
8. The service verifies device proof and durable replay consumption, transitions
   to APPROVED, issues/verifies a lease, calls Calendar, then persists APPLIED.
9. Calendar transport checks the dedicated app-owned secondary calendar and
   event ownership; preview performs no provider writes.

**Step 3 does not feed step 5.** `_save_answer()` supplies neither `RoutineDetails`
nor planning scope/permission. The Swift interview turn carries only prose,
action, revision and question key. The existing knowledge command can permit a
record for planning but cannot populate its missing structured routine fields.
The planner tests seed those fields directly. They prove the downstream planner,
not completion of the real iPhone interview-to-planning flow.

Therefore the prior instruction to answer more questions until Calendar events
appear is insufficient. No real routines or present-life facts were invented,
seeded, imported or promoted during this review.

### Authority and failure semantics

- Request signatures and approval signatures use different signing domains.
- Approval binds action, proposal, payload, enrolled device, timestamps and
  idempotency data. Voice invocation does not substitute for device approval.
- `LeaseIssuer.verify()` consumes the lease **before** Calendar execution.
  The former handoff's assertion that the lease remains intact after failure
  was incorrect.
- A provider failure leaves a proposal APPROVED. Reapproval of that proposal
  is rejected as stale. Moving the APPLIED transition later prevents false
  success; it does **not** implement recovery.
- New previews contain new proposal-specific event keys. An interrupted apply
  may leave real events that a new preview duplicates. This remains unresolved.
- Synthetic mTLS tests exercise preview, approval, replay, device/signature
  rejection and provider error responses. No real Calendar mutations were made.

## Fixed by Codex in this review

| Change | Reason and evidence |
|---|---|
| Reject LIVE `code.execute` in Commander before credential issuance/runtime dispatch | CODE previously bypassed the protected-capability checks even for a configured LIVE runtime. Added a regression asserting denial and zero broker/runtime calls. Live code execution remains disabled pending a governed implementation. |
| Preserve sensitivity and PRIVATE-only retrieval when reviewing non-imported private evidence | The old correction branch could demote RESTRICTED/SENSITIVE evidence to PERSONAL/CONVERSATION. Added regressions for both classifications. Imported reconciliation already had classification preservation. |
| Bind interview corrections to the three evidence records actually displayed | The prompt displayed at most three notes but previously corrected/superseded every matching record. Added a regression retaining the undisplayed record. |
| Content-free invalid-timestamp and duplicate-key errors | Import errors now use bounded diagnostics. Invalid timestamps leave memory unchanged; regression added. The duplicate-key loader remains a SafeLoader subclass, with explicit typed narrowing. |
| Repair configured lint/type/format failures | Initial Ruff: 11 errors; configured `basedpyright`: 29 errors. Corrected loader typing, test JSON narrowing, default arguments, SQL/string formatting and scoped complexity issues. No weakening of project-wide checks. |
| Correct transient-apply guidance | A transient failure can follow earlier successful writes. Responses direct the user to inspect Calendar before retrying rather than assume no changes occurred. |

## Inherited but inspected

- Bootstrap importer is bounded, transactional and provenance-bearing; imported
  confirmation claims do not become user confirmation or planning authority.
- `week_planning_live.py` explicitly wires the real Calendar adapter, Keychain
  token boundary and process-local lease signing key when enabled.
- Native week-plan UI retains the preview, requires an explicit approval action,
  blocks repeated busy taps, and reports uncertain apply outcomes conservatively.
- `voice/` contains a typed realtime tool allowlist, session infrastructure and
  a device-approval planning bridge. Their existence does not establish native
  app integration or physical continuous-voice acceptance.
- Commander has bounded request/scope/budget/timeout contracts, credential gates
  and protected dispatch. Sandbox workers are inert. There is no implemented
  conversational READ WORKER STATUS / STEER WORKER product surface in the inspected
  relay/app; their distinct authority levels must be designed before enabling it.
- Energy check-ins enforce consent and timing suppression. Learning code has
  proposal lifecycle operations. These are component capabilities, not evidence
  of an integrated proactive assistant on the iPhone.
- Both encrypted iOS outbox and conversation-cache writes already use
  `.completeFileProtection`. The inherited backlog's implication that it was
  missing was not supported by the code.

## Verification evidence

All commands run from the repository root. Original handoff PASS claims were
not used as evidence.

| Check | Observed result |
|---|---|
| Initial focused planning/relay/interview/knowledge tests | 48 passed |
| Initial `uv run pytest -q` | 399 passed, 30 PostgreSQL-only skips |
| `swift test --package-path ios/SecretaryApp` | 73 passed |
| `scripts/verify-foundation.sh codex-foundation-20260914` | Passed: contracts, format, lint, types, Python, Swift tests/build and live dry-run HTTP probes |
| `uv run scripts/verify_lifeos.py --local-only --with-postgres --run-id codex-reviewed-20260914` | Local verification passed; release readiness false. At that checkpoint Python: 402 passed/30 skipped; temporary PostgreSQL: 36 passed; F3 workflows and F4 humane-use checks passed. |
| Follow-up interview/import regressions | 20 passed, including two tests added after that configured run |
| Final Python regression (`codex-checkpoint-20260914/F2/python.log`) | 404 passed, 30 PostgreSQL-only skips |
| Final format/lint/types after a test-list annotation correction | 233 files formatted; Ruff clean; basedpyright 0 errors, 0 warnings |
| Unsigned generic iOS `xcodebuild` | BUILD SUCCEEDED; this compiles iOS-only UI omitted from macOS Swift tests |
| Secret-pattern scan | 343 nonignored files, zero findings; limited pattern scan, not proof that every possible secret or historical disclosure is absent |

Local artifacts are under `artifacts/verification/`; unsigned iOS build output
is `/private/tmp/lifeos-codex-ios-build.log`. These generated logs are ignored.
The release traceability gate remains rejected; local-only success is not a
production release or physical acceptance verdict.
The later `codex-checkpoint-20260914` configured run caught six type errors in
the newly added test's unannotated list. The list was annotated and the three
quality checks above were rerun successfully; that earlier failed report is
retained as recorded, without alteration.

## Unresolved, in dependency/risk order

1. **P0/P1:** Complete review of proposal ownership, stale planning inputs,
   Calendar execution/reconciliation and cross-subsystem authority boundaries.
   The reviewed paths/tests are useful evidence, not an exhaustive security verdict.
2. **P1:** Implement the missing current-life-to-structured-planning confirmation
   path on iPhone. It must preserve provenance, current validity, privacy and
   explicit planning permission, and show the user exact scheduling fields.
   Add an integration test beginning with real interview API inputs rather than
   direct routine seeding. This is an engineering gap, not a request for Jared
   to keep answering questions against the current disconnected flow.
3. **P1:** Handle interrupted/partial Calendar application without duplicate
   events or implying that a fresh preview is sufficient recovery.
4. **P2/P3:** Native conversation lifecycle needs further review. In the inspected
   `ConversationSession`, synchronization clears active voice state; the native
   conversation path explicitly says assistant replies are not connected.
   Interview speech has its own pause handling. These are separate mechanisms.
5. **P4/P5:** No integrated Driving Mode or general Capability Router product
   was found in the app/relay. Do not describe those features as delivered.
6. **P6:** Worker observation and steering remain unimplemented product paths.
   Keep status reads distinct from structured, audited steering; never route
   conversation text directly to shell execution. LIVE CODE is now denied.
7. **P7/P8:** Proactive integration and remaining hardening are not complete.
   Import source-ID changes can bypass source-ID-based tombstone deduplication;
   no comprehensive repair for this was made in the current checkpoint.

## Physical/user verification required

**Do not mark READY_FOR_USER_VERIFICATION yet.** The software connection in P1
is missing. This checkpoint requires further engineering before a meaningful
physical acceptance procedure can be issued.

Prior installation, live OAuth, bootstrap assertion counts, relay availability
and device connectivity were inherited claims; they were not independently
revalidated against private runtime state in this review.

Once the software gap and execution recovery are resolved, the acceptance gate
remains Jared personally completing:

1. iPhone Know Me/current-life reconciliation and review of structured schedules.
2. Plan My Week; inspect the actual dates, times, gaps and Calendar changes.
3. Approve the exact proposal once.
4. Confirm the expected real events in the LifeOS-owned Google Calendar.

No physical PASS or real-event acceptance is claimed here.
