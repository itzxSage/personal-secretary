# Codex Handoff — LifeOS Week Planning Vertical

## Mission

Deliver working end-to-end LifeOS vertical on physical iPhone:
**Invoke → Speak "Plan my next seven days" → Real Mac Life Engine plans → Approve → Real Google Calendar updated**

## Current State (as of 2026-09-13)

### ✅ Complete (Astra + Sisyphus)

**Backend — Life Interview / User Understanding**
- `life_knowledge.py` — KnowledgeState, KnowledgeKind, LifeDomain, KnowledgeDetails with temporal validity
- `life_interview.py` — Resumable interview: begin/advance/pause/resume, review mode, sensitive gating
- `interview_catalog.py` — 22 domains, comprehensive questions, permission-gated sensitive topics
- `life_model.py` — KnowledgeView with conflict detection, planning_knowledge() projection
- `knowledge_commands.py` — Correct/confirm/private/forget/exclude_planning actions

**Backend — Week Planner**
- `planner_models.py` — WeekPlanRequest (10,080-minute validation, DST-safe)
- `planner.py` — propose_week() reusing propose_day() contract
- `planner_placement.py` — Score-based best_placement optimization
- `tests/test_week_planner.py` — 3 tests passing (DST, cross-day deps, validation)

**Backend — Relay API**
- `relay_api.py` — /v1/interview, /v1/knowledge endpoints with mTLS auth

**Backend — Authority/Lease/Calendar (Existing Patterns)**
- `ProposalLifecycle` (authority.py) — PROPOSED → APPROVED → APPLIED with payload hashing
- `LeaseIssuer` (leases.py) — One-shot payload-bound capability leases
- `GoogleCalendarAdapter` (google_calendar.py) — dry_run/apply to LifeOS Proposed calendar only
- `RolloutCoordinator` (rollout.py) — Reference pattern for propose/approve/apply

**iOS Client**
- `AppBrand.swift`, `LifeDesign` — Design tokens
- `LifeInterviewView.swift` — Full interview UI (voice, typing, skip, progress)
- `NativeInterviewVoice.swift` — TTS/STT with Speech framework
- `LifeInterviewContract.swift` — Codable DTOs
- `SignedConversationRelay.swift` — Interview HTTP methods
- `ConversationSession.swift` — loadInterview(), advanceInterview()
- `ConversationShellView.swift` — "Know Me" button + sheet

**Tests**
- 359 tests pass (30 skipped — PostgreSQL-only)

---

## 🔄 In Progress — Wave 1: Week Planning Service

**Status:** About to start. Plan agent produced detailed implementation plan.

### Next Implementation Tasks (Wave 1 — Parallel)

| Task | File | Status |
|---|---|---|
| Backend: week_planning.py service + tests | `mac/secretary_service/src/secretary_service/week_planning.py` + `tests/test_week_planning.py` | **READY TO START** |
| iOS: WeekPlanningContract.swift + relay methods | `ios/SecretaryApp/SecretaryApp/Client/WeekPlanningContract.swift` + `SignedConversationRelay.swift` | **READY TO START** |

### Key Patterns to Follow

**Backend Service Pattern** (from `voice/planning_bridge.py`):
```python
# 1. Create proposal via ProposalLifecycle
proposal = ProposalRecord(proposal_id=..., action_class="calendar.apply", payload=..., state=PROPOSED)

# 2. Approve with device-signed approval
state = lifecycle.approve(proposal, approval, matrix)

# 3. Issue lease
lease = lease_issuer.issue(approved, LeaseRequest(capability="calendar.apply", ...))

# 4. Verify lease + apply
lease_issuer.verify(lease, approved)
backend.apply_replan(approved)  # calls GoogleCalendarAdapter.apply()
lifecycle.apply(approved, lease)
```

**Proposal Payload**: Must be canonical JSON that `GoogleCalendarAdapter.dry_run()` and `.apply()` accept — a `CalendarPlan` with `CalendarEventDraft[]`.

**Knowledge → PlanActivity Conversion**:
- Only CONFIRMED/OBSERVED `RoutineDetails` with sufficient deterministic fields
- Fixed routines → PROTECTED/FIXED blocks (non-calendar-eligible)
- Under-specified routines/projects → `PlanningGap(reason="unknown_time")`
- OpenLoopDetails → NEVER enter planner

**iOS Contract Pattern** (from `LifeInterviewContract.swift`):
```swift
public struct WeekPlanProposal: Codable, Sendable {
    public let proposalID: UUID
    public let status: String
    public let blocks: [PlanBlock]
    public let explanations: [Explanation]
    public let gaps: [PlanningGap]
    public let calendarDryRun: CalendarPreview
}
```

---

## 📋 Full Implementation Plan (Waves 1-4)

### Wave 1: Domain TDD (Parallel)
- **Backend**: `week_planning.py` + `test_week_planning.py` (TDD RED→GREEN)
- **iOS**: `WeekPlanningContract.swift` + `SignedConversationRelay` methods + tests

### Wave 2: Relay Endpoints (After Wave 1 contracts stable)
- **Backend**: `relay_api.py` — POST `/v1/week-plan`, POST `/v1/week-plan/{id}/approve`
- **iOS**: `SignedConversationRelay.previewWeekPlan()` + `approveWeekPlan()`

### Wave 3: Session + UI (After Wave 2 tested)
- **iOS**: `ConversationSession.weekPlanProposal` state + `previewWeekPlan()`/`approveWeekPlan()`
- **iOS**: `ConversationShellView` — "Plan My Week" button + proposal approval sheet

### Wave 4: Integration + Physical iPhone
- Full pytest + swift test regression
- Physical iPhone install via Xcode
- Tailscale HTTPS to Mac relay
- End-to-end: tap button → see proposal → approve → verify Google Calendar

---

## 🛡️ Architecture Invariants (Must Preserve)

1. **Proposal → Approval → Lease → External Action** — Never bypass
2. **Payload-bound approvals** — Approval hash must match proposal payload hash
3. **LifeOS-owned calendar only** — Never primary calendar, never external events
4. **Deterministic planning** — No LLM, no fabricated durations/times
5. **KnowledgeState discipline** — INFERRED never silently → CONFIRMED
6. **Replay resistance** — Consumption store keys for approval/lease/apply
7. **Device identity** — All approvals device-signed, verified against enrollment

---

## 🎯 Physical Device Gates (Will Need You)

1. **Apple signing** — Select development team in Xcode
2. **iPhone plugged/unlocked** — Trust developer certificate
3. **Microphone permission** — Accept on first voice use
4. **Google OAuth** — Consent for calendar access
5. **Tailscale** — Verify Mac/iPhone connectivity

---

## 🚀 Start Commands

```bash
# Backend tests
cd mac/secretary_service && uv run pytest -q tests/test_week_planning.py

# iOS tests
swift test --package-path ios/SecretaryApp

# Full regression
cd mac/secretary_service && uv run pytest -q
swift test --package-path ios/SecretaryApp

# Physical iPhone
# 1. Open ios/SecretaryApp/SecretaryApp.xcodeproj in Xcode
# 2. Select your team, plug iPhone, build & run
# 3. Verify Tailscale connectivity to Mac relay
```

---

## 📁 Key Files to Know

| Purpose | File |
|---|---|
| Week planning service (new) | `mac/secretary_service/src/secretary_service/week_planning.py` |
| Week planning tests (new) | `tests/test_week_planning.py` |
| iOS DTOs (new) | `ios/SecretaryApp/SecretaryApp/Client/WeekPlanningContract.swift` |
| Relay API (modify) | `mac/secretary_service/src/secretary_service/relay_api.py` |
| iOS Relay (modify) | `ios/SecretaryApp/SecretaryApp/Client/SignedConversationRelay.swift` |
| Session state (modify) | `ios/SecretaryApp/SecretaryApp/App/ConversationSession.swift` |
| Shell UI (modify) | `ios/SecretaryApp/SecretaryApp/App/ConversationShellView.swift` |
| Authority pattern | `mac/secretary_service/src/secretary_service/authority.py` |
| Lease pattern | `mac/secretary_service/src/secretary_service/leases.py` |
| Calendar adapter | `mac/secretary_service/src/secretary_service/google_calendar.py` |
| Voice bridge reference | `mac/secretary_service/src/secretary_service/voice/planning_bridge.py` |
| Agent handoff | `docs/agent-handoff-current.md` |

---

## 🧠 Mental Model for Implementation

The week planner is **deterministic** — it takes a `WeekPlanRequest` (activities with constraints) and produces a 10,080-minute plan. The new service layer:

1. **Preview**: Reads Life Model → builds WeekPlanRequest from CONFIRMED routines → `propose_week()` → `GoogleCalendarAdapter.dry_run()` → persists `ProposalRecord` → returns proposal + gaps + dry run
2. **Approve**: Validates device-signed approval → `ProposalLifecycle.approve()` → issues lease → `GoogleCalendarAdapter.apply()` → persists APPLIED

**No NLP, no intent recognition, no conversation keyword matching.** A dedicated "Plan My Week" button triggers the flow.

---

## 🔑 Test Patterns to Use

```python
# Fixtures (conftest.py)
clock: FakeClock          # clock.now(), clock.advance(td)
store: EncryptedStateStore

# Context (goals_memory_helpers.context)
context(clock, "correlation-id") -> TransitionContext

# Knowledge records (test_life_knowledge.assertion)
assertion(clock, 1, content="Jared", key="identity.name", domain=LifeDomain.IDENTITY, ...)

# Calendar adapter (test_google_calendar.make_adapter)
make_adapter(clock) -> (GoogleCalendarAdapter, GoogleCalendarSandbox)
```

---

## ⚠️ Don't Break

- Existing 359 tests
- Authority/lease/calendar invariants
- mTLS relay authentication
- Encrypted store transactions
- Life Interview subsystem

---

**Ready for Wave 1 implementation.** The plan is decision-complete. Start with backend `week_planning.py` TDD and iOS `WeekPlanningContract.swift` in parallel.