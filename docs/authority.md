# Authority, Enrollment, and Capability Leases

The policy layer gates proposal approval before calendar, voice planning and
rollout consumers execute actions. Enrollment supports SQLCipher persistence,
and calendar/rollout lifecycle replay markers persist there. Isolated policy
tests can use in-memory stores. Voice leases and transactional recovery across
approval and external execution still require production integration.

## Threat model

| Threat | Mitigation |
| --- | --- |
| Invocation-method elevation: a voice/channel/worker source claims a higher authority tier | Required tier is derived only from the action class; the invocation method never changes it |
| Forged approval: an approval record fabricated without an enrolled device | Consequential approvals require a device-signed proof; voice/channel proofs are never accepted |
| Replay: an approval or lease reused for a different payload or after consumption | Payload-hash binding plus one-shot consumption |
| Expiry bypass: an approval or lease used after its deadline | Every transition re-checks expiry against the injected clock |
| Interrupted spoken approval: a voice approval that never completes | Voice can request but never prove; proof must be device-signed |
| Key rotation: signatures made with a rotated-out signing key | Lease verification consults only the current signing key |
| Forged/revoked device: an attacker presents an unknown or revoked mTLS identity | Device registry verifies fingerprint and active state before any approval is attributed |

## Authority tiers

- `observe` - read-only reporting (automatic).
- `recommend` - proposals and previews (automatic).
- `act` - reversible internal changes (approval required).
- `external` - outbound side effects (approval required).

The approval matrix maps each action class to its required tier and the proofs
that may approve it. `can_request` is open to every invocation method;
`can_approve` accepts only the proofs listed for the action's tier. Voice and
channel sources may request but never prove approval identity; only an enrolled
device can produce a `device_signed` proof.

## Proposal lifecycle

`proposed -> approved -> applied -> reverted | reconciled`

Every transition requires a payload-bound fact (approval, lease, rollback, or
reconciliation) carrying the proposal's payload hash, an expiry, an idempotency
key, and a nonempty audit actor. Replays and expired facts fail closed. A
proposal that never reaches `approved` cannot receive an execution lease.

### Reset: `approved -> proposed`

An approved proposal whose execution was interrupted may be reset back to
`proposed` so it can be re-approved and re-applied. The reset transition
requires:

- The proposal is in `approved` state (never `applied`; an applied proposal must
  be reverted or reconciled first).
- Reconciliation has confirmed zero provider effects (no events owned by the
  proposal exist in the calendar).
- A device-signed `reset_request` proof carrying the same authority as the
  original approval (same action class, payload hash, expiry, and enrolled
  device signature).
- A fresh idempotency key; the reset proof and the original approval markers are
  consumed so neither the reset nor the approval can be replayed.

After reset the proposal returns to `proposed` and requires a new approval and a
new execution lease before it can be applied again. The consumed execution lease
from the interrupted attempt remains consumed and cannot be replayed.

## Device enrollment and mTLS identity

`DeviceRegistry` enrolls devices by public-key fingerprint and revokes them on
demand. `verify_mtls_identity` accepts only enrolled, active devices whose
presented fingerprint matches; forged and revoked devices fail closed. A revoked
device cannot reconnect or produce a device-signed approval.

Approval authorization verifies an Ed25519 signature using a separately enrolled
approval public key. A device ID, fingerprint or `DEVICE_SIGNED` enum alone is not
proof. Legacy enrollments without an approval key fail closed. The signature
binds `lifeos.approval.v1`, the action class, and every approval field except
`signature`, encoded as sorted compact ASCII JSON. Changed actor, timestamps,
payload, device or idempotency identity invalidates the signature. Verification
uses the cryptography library's [Ed25519 API](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/).

`fixture_authority.py` contains deliberately public deterministic signing material
for tests only. Never enroll its key in a real deployment. The authority module
only verifies signatures and does not import the fixture private key.

The encrypted `enrolled_devices` table retains public keys and revocations across
restarts and is read on every verification. `authority_consumption` claims the
proposal, fact and idempotency markers atomically; duplicate claims roll back all
new markers. Calendar and rollout consumers use this store. These tables are
included in encrypted database backups. Full lease/execution crash reconciliation
remains a release requirement.

## Capability leases

`LeaseIssuer` issues one-shot, payload-bound capability leases signed by the
current rotating signing key. Verification rejects forged signatures,
rotated-out keys, payload mismatches, expired leases, and replays. Key rotation
fails closed: signatures made with a rotated-out key are rejected immediately.
