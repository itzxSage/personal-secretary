# Privacy, Export, Revocation, and Recovery

Task 18 remains an offline sandbox exercise. It does not authorize a connector, worker,
fabric runtime, provider, calendar write, message send, or production rollout. Those
capabilities remain disabled through F1-F4 and require later explicit owner authorization.

## Privacy and export

- SQLCipher state, backup ciphertext, recovery envelopes, and Keychain material never enter
  reports, source control, screenshots, or support bundles.
- `StateExporter.redacted` is the supported export during this stage. It emits record IDs,
  kinds, states, and a redaction marker, then appends an audit fact. It does not export
  payloads, connector references, credentials, tokens, or key material.
- A user-requested deletion invalidates every backup known to contain the record before a
  clean replacement backup is created. An invalidated or expired backup is ineligible for
  recovery even if its ciphertext still exists.
- Audit reports contain counts, hashes, states, and denial reasons only. Raw communication,
  transcript, health, calendar, and worker artifact content stays out of evidence.

## Revocation order

For a lost device, suspected token compromise, connector incident, or recovery drill:

1. Keep production capabilities disabled and stop the adapter or relay process.
2. Revoke the device mTLS enrollment and every affected channel binding.
3. Invalidate active session tokens and rotate service signing credentials by opaque
   Keychain reference. Never print or export the replacement material.
4. Verify that the old device, binding, and token cannot reconnect before considering a
   restore or adapter restart.
5. Apply the current deletion and revocation ledger to restored state. A backup must not
   revive a revoked pairing, connector, consent, or deleted record.
6. Verify the encrypted audit chain and retain content-free incident evidence.

`VoiceSessionService.resume` rechecks the device mTLS enrollment before replacing a token.
A token held by a revoked device therefore cannot reconnect, including inside the normal
30-second reconnect window. OpenClaw requests independently require both an enrolled
service-account device and an active exact channel binding.

## Backup restore and database-key loss

Maintain a sealed inventory outside the live service with backup ID, creation/expiry time,
opaque recovery-envelope reference, and verification date. The inventory must contain no
key bytes. Exercise it quarterly.

1. Confirm the backup is active, owner-selected, within retention, and absent from the
   deletion invalidation ledger.
2. Preserve the inaccessible database as incident evidence. Do not generate a replacement
   key over it or overwrite it implicitly.
3. Resolve the backup wrapping key and replacement database key from their approved opaque
   stores. `BackupRecovery` unwraps only in memory, verifies the source audit chain, writes
   a temporary SQLCipher database under the replacement database key, verifies it again,
   and atomically publishes a previously nonexistent target.
4. Revoke devices, bindings, and service tokens before any adapter restart. Retain the
   existing audit HMAC key so the historical chain remains verifiable; audit-key rotation
   requires a separately reviewed chain-anchoring migration.
5. Replace the offline recovery envelope and record content-free recovery evidence. Keep
   fabric and workers disabled.

Run the hermetic procedure with `scripts/rollout_recovery_drill.sh` as documented in
`docs/verification.md`. The drill uses synthetic keys and temporary databases only.
