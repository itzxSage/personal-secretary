# Capability maturity

Updated 2026-09-11. This is the current implementation map. Historical plan checkboxes
are not release evidence. “Local” includes unit/contract/synthetic integration tests;
“live” means an observed real integration, with its scope stated explicitly.
No row currently establishes production readiness.

| Capability | Current implementation/evidence | Next required evidence |
| --- | --- | --- |
| Canonical state, goals, memory, learning | Typed models, SQLCipher repositories, local tests and recovery fixtures | Cloud repositories, migration parity and restored deletion semantics |
| Planner | Deterministic greedy planner; regression/generated-case tests for dependency timing, hard deadlines, fixed constraints and DST travel | Global optimization benchmark; user-reviewed real planning scenarios |
| Authority | Payload signatures, enrollment, replay protection and persisted calendar consumption | Concurrent cloud transactions and crash-to-external-effect reconciliation across every executor |
| PostgreSQL migration | Encrypted domain/device/outbox repositories; real local concurrency and process-crash tests; atomic approval-to-job service | Remaining canonical repositories, KMS, runtime DB roles, cloud ingress and real provider reconciliation |
| iPhone text relay | Physical phone ↔ Mac mTLS/signature delivery and deletion observed on 2026-09-11 | Cloud ingress/account enrollment, Mac-off operation, native pairing/recovery |
| Google Calendar | Real REST/OAuth adapter; live refresh and dedicated-calendar create/readback | Real event create/update/reconcile/delete, revocation and persistent service wiring |
| Assistant replies | Proposal-only text interpreter and mocked-provider tests | Consented live inference wired to the conversation journal and app |
| Realtime voice | Session/ordering/privacy contracts and simulated conversations | Microphone → cloud relay → provider → playback, interruption and reconnect on device |
| OpenClaw/channels | Pinned boundary, typed adapters, local adversarial fixtures | Actual sidecar isolation, live reconnect, membership and cross-channel delivery |
| Hermes Agent | Optional pinned planning interpreter, bounded HTTP transport and negative local HTTP fixture tests | Pinned runtime deployment, enforced isolation/retention, canary and rollback evidence |
| Worker workforce | Lease/budget/credential gates and simulated effects | Real isolated worker operation, cancellation, escape and recovery evidence |
| Executive UI | Development conversation shell and public invocation handlers | Onboarding, connections, approvals, why/explanations, goals, timeline and recovery UX |
| Cloud operations | Target architecture documented | Deployed isolated tenant, KMS, backup/restore, alerts, rollout and disaster recovery |
| Security/release | Local adversarial tests, strict QA, pattern scanner, immutable CI action refs | Broader SAST/dependency/SBOM/container/API fuzzing; deployed threat model and independent review |

Move a capability through specified → implemented → local verified → live verified
→ production hardened → production verified. Record which boundary was actually
tested; a fixture crossing an interface does not establish that the real service
implements it. Record source commit/hash, command, environment, result and limitations
for each promotion. Test totals and this table never authorize production capabilities.

The cloud design is in [cloud-architecture.md](cloud-architecture.md). The automated
release gate remains [release-requirements.json](release-requirements.json); absent
evidence keeps it closed. Prior local reports are historical after source changes.
