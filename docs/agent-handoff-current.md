# Agent Handoff — Current Session

## Current Mission

Deliver the first working end-to-end LifeOS vertical on a physical iPhone:
invoke LifeOS → speak "Plan every minute of my next seven days" → real Life Engine
plans the week → user approves → real Google Calendar updated.

## Definition of Done

Physical iPhone invocation → real Mac-hosted Life Engine → deterministic week plan →
proposal visible to user → approval → real Google Calendar events → verify on device.

## Completed Tonight (Astra)

### Life Interview / User Understanding (Backend — Complete)
- `life_knowledge.py` — KnowledgeState, KnowledgeKind, LifeDomain, KnowledgeDetails with
  temporal validity, evidence, provenance, staleness, conflict detection
- `life_interview.py` — Resumable interview: begin/advance/pause/resume, answer/skip,
  stale review, sensitive domain gating, open loop sweep, provenance tracking
- `interview_catalog.py` — 22 domains, comprehensive questions, sensitive domain list
- `life_model.py` — KnowledgeView with explanations, conflict detection, planning projection
- `knowledge_commands.py` — Correct/confirm/private/forget/exclude_planning actions

### Week Planner (Backend — Complete)
- `planner_models.py` — WeekPlanRequest (10,080-minute validation)
- `planner.py` — propose_week() using existing propose_day() contract
- `planner_placement.py` — Score-based best_placement optimization

### Relay API (Backend — Complete)
- `relay_api.py` — /v1/interview, /v1/interview/turn, /v1/knowledge, /v1/knowledge/{id}
  endpoints integrated with existing mTLS auth

### iOS Client (Complete)
- `AppBrand.swift` — Display name + design tokens
- `LifeInterviewContract.swift` — Codable types for interview API
- `NativeInterviewVoice.swift` — Full TTS/STT with Speech framework
- `LifeInterviewView.swift` — Interview UI with voice, typing, skip, progress
- `SignedConversationRelay.swift` — Interview HTTP methods
- `ConversationSession.swift` — Interview load/advance methods
- `ConversationShellView.swift` — "Know Me" button + sheet

### Tests (Passing)
- `test_life_interview.py` — 5 tests: catalog, answer/resume, stale rejection, review, sensitive gating
- `test_week_planner.py` — 3 tests: 10080-min coverage, cross-day deps, validation
- All 359 tests pass (30 skipped — PostgreSQL-only, expected)

## Current Work

Astra stopped after implementing the Life Interview subsystem and week planner.
The next step is wiring the seven-day planning request through the conversation path.

## Files Changed (Uncommitted)

### New Files (Backend)
- `mac/secretary_service/src/secretary_service/life_knowledge.py`
- `mac/secretary_service/src/secretary_service/life_interview.py`
- `mac/secretary_service/src/secretary_service/life_model.py`
- `mac/secretary_service/src/secretary_service/interview_catalog.py`
- `mac/secretary_service/src/secretary_service/knowledge_commands.py`
- `mac/secretary_service/src/secretary_service/postgres_conversations.py`
- `mac/secretary_service/src/secretary_service/migrations/009_life_model_backup_coverage.sql`
- `tests/test_life_interview.py`
- `tests/test_life_knowledge.py`
- `tests/test_postgres_conversations.py`
- `tests/test_week_planner.py`

### New Files (iOS)
- `ios/SecretaryApp/SecretaryApp/App/AppBrand.swift`
- `ios/SecretaryApp/SecretaryApp/App/LifeInterviewView.swift`
- `ios/SecretaryApp/SecretaryApp/App/NativeInterviewVoice.swift`
- `ios/SecretaryApp/SecretaryApp/Client/LifeInterviewContract.swift`

### Modified Files
- Backend: planner.py, planner_models.py, planner_placement.py, relay_api.py,
  memory.py, memory_repository.py, postgres_store.py, postgres_schema.sql,
  google_calendar_contract.py, backup_catalog.py, storage.py
- iOS: ConversationSession.swift, ConversationShellView.swift,
  SignedConversationRelay.swift, project.yml, project.pbxproj, Info.plist
- Scripts: google_calendar_oauth.py, verify_postgres.py
- Tests: test_conversation_relay.py, test_google_calendar_live.py

## Important Architecture Invariants

- Life Engine owns canonical state; OpenClaw is subordinate channel infrastructure
- Proposal → approval → execution lease → external action (never bypassed)
- KnowledgeState never silently promotes INFERRED → CONFIRMED
- Conversation state separate from channel state
- Deterministic, explainable scheduling over globally optimal
- Physical acceptance: real Google Calendar events visible after approval

## Tests / Verification Run

- `uv run pytest -q` — 359 passed, 30 skipped (22.91s)
- All new tests (life_interview, week_planner) pass

## Known Failures

None. All tests green.

## Physical Device / User Gates

- Apple signing / development team selection
- iPhone plugged/unlocked, developer certificate trust
- Microphone permission acceptance
- Google OAuth consent
- Tailscale connectivity verification

## Next Steps

1. Wire seven-day planning request through conversation path
2. Implement proposal generation that reads Life Model knowledge
3. Implement proposal → approval → Google Calendar execution
4. Verify Mac-hosted backend runs and is accessible via Tailscale
5. Build/install/test on physical iPhone
6. Resolve signing/network/permission issues

## Start Here

The Life Interview subsystem is complete and tested. The week planner produces
10,080-minute plans. The relay API exposes interview and knowledge endpoints.
The iOS client has voice, typing, and progress UI.

The missing link: when the user says "Plan my next seven days" in conversation,
LifeOS needs to:
1. Recognize the intent
2. Gather relevant Life Model knowledge
3. Build a WeekPlanRequest
4. Generate the proposal
5. Present it for approval
6. Execute approved operations on Google Calendar

This requires a conversation intent router that connects the existing pieces.
