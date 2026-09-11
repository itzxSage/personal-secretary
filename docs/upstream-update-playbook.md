# OpenClaw Upstream Update Playbook

OpenClaw is an out-of-process transport adapter pinned to reviewed revision `befc0c24`.
This playbook never fetches, imports, or enables an upstream candidate automatically.
Production fabric and workers remain disabled through Task 18 and F1-F4.

## 1. Dry run

1. Record the candidate revision in a redacted fixture; do not place credentials or raw
   channel content in it.
2. Compare the candidate with the reviewed pin, Conversation API contract, endpoint
   allowlist, capability allowlist, channel matrix, license/provenance record, and privacy
   behavior.
3. Reject a changed endpoint, capability expansion, direct state/key access, unsupported
   channel promotion, incompatible revision, external operation, or missing rollback fact.
4. Produce only a deterministic `RolloutPreview`. A dry run performs zero state mutation
   and zero network or external operations.

## 2. Proposal and approval

`RolloutCoordinator.propose` accepts only a compatible, reversible `sandbox` preview whose
fabric, workers, and production capabilities are all false. It persists an encrypted
capability record with `granted=false`, which starts the append-only audit trail.

Approval requires a payload-bound `rollout.apply` proposal and a current device-signed
proof from an enrolled owner device. Voice and channel statements are requests, not proof.
A revoked device, stale payload, expired proof, or replay fails closed.

## 3. Reversible apply

Issue an exact one-shot lease for capability `rollout.stage` and worker `lifeos-rollout`.
The apply stage verifies the lease signature and binding, then records only the inert
sandbox stage. It does not construct a live worker runtime, grant a capability, contact
OpenClaw, or change the pinned production revision.

Verify after apply:

- proposal state is `applied`;
- persisted capability remains `granted=false`;
- fabric, workers, and production capability flags remain false;
- endpoint and capability allowlists are unchanged;
- external operation count is zero;
- encrypted audit chain verifies.

## 4. Downgrade rollback

Use a current payload-bound rollback fact. Rollback records `reverted` as a new immutable
version and never deletes prior proposal, approval, apply, or audit evidence. Confirm the
persisted capability is still `granted=false`, no worker runtime is configured, and revoked
tokens and bindings still fail authentication.

If any verification fails, leave production disabled, revoke adapter credentials, preserve
the report, and investigate from the pinned revision. There is no implicit promotion or
automatic retry into production.

## 5. Later promotion gate

Completing this playbook is not authorization to deploy. Promotion requires F1-F4 evidence,
a separate owner go/no-go decision, and an explicit later change. No Task 18 fixture,
approval, lease, environment variable, or report can turn on a production capability.
