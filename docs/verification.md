# Foundation Verification

Run the complete clean-checkout-equivalent gate:

```bash
scripts/verify-foundation.sh local-$(date -u +%Y%m%dT%H%M%SZ)
```

The gate checks generated contracts, Python formatting/lint/types/tests, Swift tests/build,
then starts the real no-connectors service and curls matching, mismatched, and malformed
contract headers. Redacted evidence and `foundation-report.json` are written beneath
`artifacts/verification/<run-id>/`. The script terminates the service and writes
`cleanup.txt`; no database is created or migrated.

Run the Todo 2 state gate separately:

```bash
scripts/verify-domain-state.sh local-state-$(date -u +%Y%m%dT%H%M%SZ)
```

It runs targeted and full Python tests, format/lint/type checks, generated-contract checks,
and Swift tests/build. Its redacted report records encryption, missing-key, audit-tamper,
retention, deletion, backup invalidation/restore, export, and fake-provider probes. Test
databases and backup ciphertext remain in pytest temporary directories and are cleaned;
neither is copied into verification artifacts.

Run the Task 18 migration, restore, revocation, and rollout drill with:

```bash
scripts/rollout_recovery_drill.sh \
  --fresh fixtures/fresh-state \
  --migrated fixtures/secretary-v2 \
  --backup fixtures/encrypted-backup \
  --openclaw-fixture fixtures/openclaw
```

It exits zero only when fresh and migrated projections equal the checked-in golden result,
database-key loss fails closed before a verified re-encrypted restore, approved sandbox
apply rolls back with fabric/workers disabled and audit intact, production proposal is
rejected, and a revoked device token cannot reconnect. The report under
`artifacts/verification/task-18-local/` contains no database, backup, key, token, or raw
fixture content.
