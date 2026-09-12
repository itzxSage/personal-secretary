# One-week delivery target

Owner direction: work daily toward production within one week; keep cloud costs
low without undermining effective operation. This replaces the earlier speculative
23–26-week schedule. The dates are a work allocation, not evidence of readiness.
The full LifeOS vision remains in scope; unfinished capabilities stay disabled.

## Delivery sequence

| Day | Primary outcome | Evidence required |
| --- | --- | --- |
| 1 | Transaction boundaries, cloud deployment design, cost model | Rollback and isolation tests; reviewed account/region/identity choices |
| 2 | PostgreSQL persistence and durable job execution | Same domain invariant suite on both backends; atomic approval/state/audit/outbox; crash reconciliation |
| 3 | Synthetic cloud cell and phone enrollment | Authenticated phone text with Mac off; revoked device denied; no bootstrap secrets |
| 4 | Complete conversation-to-calendar workflow | Real reply, typed proposal, explicit approval, single provider effect, external-change reconciliation |
| 5 | Voice and daily-use iPhone experience | Speak, receive response, interrupt, reconnect; onboarding, approvals, explanations, recoverable errors |
| 6 | Reliability and adversarial exercises | Restore with deletion/revocation preserved; provider outage; concurrent replay; key denial; focused security review |
| 7 | Release candidate and owner acceptance | Reproduce enabled workflows on physical phone; source-bound deployment evidence; rollback; explicit remaining blockers |

Days may overlap only where dependencies permit. Security and failure tests accompany
each implementation; day 6 consolidates evidence rather than starting security work.
The day-2 migration is a major uncertainty, since SQLCipher-specific persistence
spans more than domain records. Reassess the delivery forecast after that gate.
Do not remove release gates to make the calendar fit.

## Product scope and launch boundary

First complete cloud workflow: authenticated iPhone text → persistent conversation
→ assistant reply and typed plan proposal → approval → Calendar operation → durable
result and explanation. Voice builds on that path. Preserve Dream → Goal → Milestone
→ Project → Quest → Task traceability and governed memory/learning.

Gmail, meeting intelligence, research, documents, OpenClaw, and computer/code workers
follow as individual enabled workflows once the core path passes. They remain part
of the product roadmap, with no claim they can all be production-hardened in seven
days. A private owner pilot and a paying-executive launch have separate acceptance
decisions. Broad commercial launch still requires operational ownership, privacy
and distribution requirements, and independent security review. External review
and account/provider approvals have unconfirmed lead times.

## Cloud choice and cost constraints

The owner has no provider preference and prioritizes low cost. Prepare a managed
Google Cloud design first: Cloud Run, Cloud SQL PostgreSQL, workload identity,
Secret Manager, Cloud KMS, object storage, and scheduled durable job dispatch.
This is a proposed implementation target, not a provisioned deployment. Account,
billing, region, residency, domain, and the actual spending ceiling remain unset.

Reasoning: Google Cloud offers an integrated route for the existing KMS/workload
identity requirements, and Google OAuth setup already exists. Existing OAuth setup
does not establish cloud billing access or permission to migrate its tokens.

Reviewed alternatives: [Render](https://render.com/pricing) and
[DigitalOcean App Platform](https://docs.digitalocean.com/products/app-platform/details/pricing/)
offer simpler application hosting. Their base hosting prices alone do not establish
an equivalent design for tenant key control, workload identity, database recovery,
and isolated workers; compare the complete deployed configuration before selection.

Pricing references checked 2026-09-11:

- [Cloud Run](https://cloud.google.com/run/pricing): usage-based compute; free-tier
  eligibility is shared with other applicable usage and must not be assumed.
- [Cloud SQL](https://cloud.google.com/sql/pricing): the published shared-core
  db-f1-micro compute rate is $0.0105/hour (about $7.67 at 730 hours), before storage,
  backup, and network charges at the applicable region. Shared-core instances lack
  Cloud SQL SLA coverage; this is only a synthetic staging cost reference.
- [Cloud KMS](https://cloud.google.com/kms/pricing): software key versions and
  cryptographic operations are separately billed.

A complete estimate must include database storage/backups, compute/voice connection
duration, logs, artifacts, scheduled work, networking, secrets, keys, model/audio
usage, and domain costs. Price a separate resilient production database; do not
present the cheapest staging database as the executive production design.
Enforce bounded instance counts, retention, job retries, and application usage
quotas. Budget alerts alone are not spending caps. No paid resources are authorized
by an unspecified preference for low cost, and none have been provisioned here.

## Implementation evidence

First local persistence seam implemented:

- Backend-independent `DomainRecords` / `DomainUnitOfWork` protocols.
- SQLCipher domain transactions serialize writers before reading versions/audit.
- Domain create, transition, and deletion roll back on non-driver failures too.
- Nested operations use savepoints and cannot commit their enclosing domain batch.
- Domain batches expose only domain operations, not unrelated repositories that
  still own independent commits. Callers must use the returned repository within
  its context; it is not an authority or tenant boundary.

The next slice is now implemented: a real PostgreSQL adapter, encrypted device
membership, and atomic approval consumption/state/audit/outbox, with process-crash
and concurrency tests. [Migration status](postgres-migration.md) lists the remaining
canonical persistence and deployment work. Cloud readiness is not established.

The owner also requested Hermes as an optional agent backend. Its planning adapter
now uses the existing interpretation interface, with a pinned version and local HTTP
negative tests. [Agent boundaries](agent-runtimes.md) distinguishes this from a live
Hermes/OpenClaw runtime and describes the remaining isolation evidence.
