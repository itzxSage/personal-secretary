# LifeOS — Independent Review Checkpoint

## Takeover checkpoint — 2026-09-15

**Recovered plan:** [`.omo/plans/lifeos-master-plan.md`](../.omo/plans/lifeos-master-plan.md).
The plan was interrupted in Task 6; its checkmarks were audited against the
worktree rather than trusted. `43d9879` remains the last Codex-reviewed base;
the committed campaign work is `d067f45`, `f720735`, `3cf8682`, and `0e8fa03`,
with further uncommitted repairs in this worktree. Do not reset, clean, or
discard this work.

### Reconciled campaign status

| Plan item | Actual state | Evidence / limitation |
|---|---|---|
| 1 OpenAI audit | IMPLEMENTED_UNVERIFIED | Existing paid adapters remain present; `interpret_plan.py` now selects the free adapter, but no production runtime composition was proven. |
| 2–3 interview to planner | COMPLETE_VERIFIED | The signed mTLS relay path now carries its configured planning timezone. Routine extraction refuses unknown days/time/duration rather than inventing them; explicit confirmation grants planning permission. |
| 4 Calendar recovery | COMPLETE_VERIFIED (hermetic) | The real week-planning service has a recovery route, a dedicated reset signature domain, append-only recovery generations, provider reconciliation, and a cross-process execution lock. Partial application resumes only the exact original payload under a fresh signed bounded recovery proof. Physical Google-provider recovery remains unverified. |
| 5 planning vertical proof | COMPLETE_VERIFIED (hermetic) | Interview input through confirmation to deterministic preview, signed approval, sandbox Calendar writes: no direct `RoutineDetails` seed. |
| 6 Hermes runtime | COMPLETE_VERIFIED (local) | Pinned Hermes `v2026.9.7` / `2237be355906fbe6065ce1815711eee52b2d646e` runs as `com.personal-secretary.hermes`, bind-only at `127.0.0.1:8642`, authenticated by a Keychain-held API key, and reports an explicit empty API toolset. A real OpenRouter free-model request completed with no tool call. |
| 7 conversational wiring | COMPLETE_VERIFIED (synthetic) | The mTLS relay now composes only the reviewed tool-free Hermes adapter. A live synthetic two-turn conversation traversed Hermes, the Keychain-backed sidecar credential, and encrypted conversation history with zero action proposals. A separate mTLS route regression proves the iPhone endpoint authenticates each turn and supplies follow-up context. Physical speech and iPhone-to-relay delivery remain unverified. |
| 8–9 native voice / Control Center | IMPLEMENTED_UNVERIFIED | The shared direct-listening path now activates the iOS audio session before installing its microphone tap and rejects an invalid input format without terminating. It compiles in a signed device build and is installed, but physical microphone, recognition, barge-in and Control Center invocation remain unverified. |
| 10 full Control Center vertical | PARTIAL | Signed current app is installed on the connected iPhone; Hermes and the private-LAN relay are running. Control Center, microphone, STT/TTS and speech-to-Hermes behavior require physical evidence. Voice-driven Calendar planning is still not routed from Hermes to the deterministic planner/proposal boundary. |
| 11 voice approval | IMPLEMENTED_UNVERIFIED | UI routes only explicit biometric device-signed approval; voice text cannot dispatch arbitrary actions. It needs an end-to-end relay test after Hermes is real. |

### Defects reopened and repaired

- The prior interview parser fabricated daily recurrence, one-hour duration,
  8am morning starts and a high priority. It now creates a provenance-bearing
  incomplete routine draft and asks for the missing planning facts.
- The original recovery helper was disconnected from the actual week-planning
  service. Recovery now routes through that service and relay. It does not
  report external edits as success, and it cannot reset a proposal until
  reconciliation proves zero LifeOS-owned provider effects.
- A provider timeout/connection failure in iOS retains the exact approved
  proposal encrypted for recovery. Auth/stale validation errors clear that
  local recovery hold because they occur before Calendar execution.
- The initially generated iOS project omitted the encrypted recovery file;
  regenerating from `project.yml` repaired the physical iPhone build target.
- The Push to Talk and Control Center paths called native `listen()` before the
  iOS audio session was active. `AVAudioEngine.installTap` can terminate the
  process for its resulting zero-channel/zero-sample-rate input format. The
  shared listener now activates `.playAndRecord` first and reports an
  unavailable microphone format as a recoverable error.
- Device crash report `98E95B84-C496-48F7-8EA5-F66C9D79B44A` then identified
  the actual recurring trap: `SFSpeechRecognizer.requestAuthorization` invoked
  a MainActor-isolated continuation callback on a root queue. The authorization
  bridge is now `nonisolated`; only its returned status crosses back to the
  main-actor voice state.

### Executed verification

- `uv run pytest -q`: **443 passed, 30 skipped** (PostgreSQL-only skips).
- `swift test --package-path ios/SecretaryApp`: **77 passed**.
- `xcodebuild ... -sdk iphoneos ... CODE_SIGNING_ALLOWED=NO build`: **BUILD SUCCEEDED**.
- Changed Python sources plus tests: Ruff format/check and basedpyright: **passed, 0 errors**.
- `uv run scripts/verify_lifeos.py --local-only --run-id takeover-20260915`:
  **REJECT**. Its Python, contracts, secret scan, Hermes/OpenClaw pin and local
  F3/F4 gates passed; F2's whole-tree format/lint includes the unrelated,
  unformatted `tools/dev_harness/` takeover work, and F1 correctly retains
  unmet production/physical requirements. This is not evidence of a LifeOS
  source regression. A separate `scripts/relay.py` complexity lint remains to
  be repaired before accepting the whole-tree F2 result.
- Hermes isolated runtime: source SHA-256
  `c1f2401c8096e9372c46fa4ef8bdada18ed3cc84c0f5274562b86b7646ed3a87`;
  `hermes --help` passed; authenticated `/v1/capabilities` and `/v1/toolsets`
  passed with zero enabled toolsets; unauthenticated discovery returned 401;
  provider-less chat returned 500 without reporting success.
- Live Hermes proof: OpenRouter provider credential is in macOS Keychain only.
  `nvidia/nemotron-3.5-lightning:free` returned a completed tool-free response;
  the production relay uses `/Users/jrsgagne/.config/lifeos/hermes/conversation.json`
  and the local `com.personal-secretary.hermes` and
  `com.personal-secretary.staging-relay` LaunchAgents. The relay remains on the
  private-LAN address `192.168.12.133:8443`; Hermes is not LAN-exposed.
- `tests/test_hermes_conversation.py`, `tests/test_relay_https.py`, and
  `tests/test_free_model_adapter.py`: **33 passed**. The live synthetic proof
  passed with two encrypted turns and zero action proposals.
- Signed iPhone Debug build: **BUILD SUCCEEDED** and installed on the connected
  iPhone. Device-surface probe finds the app and a historic push-to-talk receipt;
  Control Center has no physical receipt yet.
- Voice crash repair: `swift test --package-path ios/SecretaryApp`: **77
  passed**; signed `xcodebuild` against device
  `00008120-00100D5036C2201E`: **BUILD SUCCEEDED**; the repaired app was
  installed with `xcrun devicectl`. The device must still exercise Push to Talk
  and Control Center to verify the AVAudioEngine path physically.
- Crash-capture repair: the device emitted `EXC_BREAKPOINT` / `SIGTRAP` on the
  background Speech authorization callback, with `_swift_task_checkIsolatedSwift`
  and `NativeInterviewVoice.start(question:)` in the faulting stack. After the
  nonisolated bridge repair, the signed build again succeeded and was installed;
  physical retest remains required.
- A second device report then identified the same isolation violation in the
  `AVAudioEngine` tap: its realtime queue executed a MainActor-inherited
  closure. The existing sendable audio sink is now passed through an explicit
  `@Sendable` tap callback. The signed app stayed alive under `devicectl`
  console supervision for more than 65 seconds after launch. Push to Talk and
  Control Center still require a person to perform the final physical action.

`READY_FOR_PHYSICAL_VERIFICATION` applies to the **voice conversation slice**:
open the installed app, add its Control Center control, grant microphone/speech
permission, invoke it, and speak two follow-up turns. The full Calendar
demonstration remains **not ready** because Hermes has no route from spoken
planning intent to the deterministic planner and payload-bound proposal flow.

Updated by Codex, 2026-09-15. Repository and fresh test results are authoritative.
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
