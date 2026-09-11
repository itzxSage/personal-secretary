# LifeOS architecture and implementation status

The macOS Python Life Engine owns SQLCipher state, canonical records and audit,
goals/memory, deterministic day plans, approval policy and reconciliation.
Google Calendar and OpenClaw are external projections/adapters. The Swift client
shares the conversation contract and encrypts its offline event outbox.

Local modules cover the 18 task areas in the pivot plan. Their tests establish
local behavior, not completion of live integration. The app queues outgoing
events locally and can opt into a signed mTLS HTTPS relay. The relay stores events
in canonical SQLCipher records with atomic ordering/audit and idempotent delivery.
Its installable Xcode project is generated, while the WebSocket/audio loop,
widget-extension packaging, and native UI/device harness remain unfinished.
Conversation content has a fixed 180-day retention deadline, an explicit signed
deletion endpoint, and an offline encrypted Swift history cache. Retention pruning
is an explicit staging maintenance command until production scheduling is configured.
Interpretation, realtime, calendar and OpenClaw E2E tests use fixture providers.
The real Responses API text interpretation adapter is also available through
`scripts/interpret_plan.py`; it has mocked-provider verification and awaits live
project configuration and integration into the persistent service.

Google Calendar now also has a fixed-host live REST transport and installed-app
OAuth bootstrap. It retains only the app-created secondary calendar ID, never lists
the user's calendars, uses ETags to reject concurrent overwrites, and keeps refresh
material in Keychain. It awaits user consent, sandbox-account validation, and wiring
to the persistent service; production event writes remain disabled.

Calendar and rollout consumers verify signed approvals and persist one-shot
markers in encrypted state. Enrollment/public keys/revocation can use the same
store. Other execution paths, especially voice lease consumption, still need
transactional persistence and crash recovery before production activation.

Three executable HTTP surfaces currently exist:

- `secretary_service.app:app`: contract-version-gated health, connectors disabled.
- `secretary_service.slice.api:app`: synthetic tomorrow-plan preview, signed
  approval, replan and rollback using SQLCipher and an in-memory calendar sandbox.
- `scripts/relay.py`: explicit staging-only conversation ingress with real TLS
  peer binding, request signatures, durable replay rejection and private membership.
  Swift delivery uses system TLS trust, refuses redirects and retains failed batches.

None is the completed production assistant. The fixture API's public
test signing key must never be enrolled in a real deployment.

The release runner hashes current source, including uncommitted files, executes
local checks and records outstanding delivery requirements. Missing native,
provider or production-authority evidence keeps release readiness false.
