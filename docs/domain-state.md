# Encrypted Domain State

Todo 2 adds a SQLCipher 4 persistence boundary without enabling connectors, policy,
approvals, or executors. `EncryptedStateStore.open` applies the key before its first
database read, requires a nonempty `cipher_version`, authenticates the schema, applies the
versioned migration, and verifies the audit chain. A missing or incorrect key fails closed.

## Keys and secrets

`MacOSKeychainKeyProvider` resolves opaque references through the native macOS Keychain.
The expected production references are `database-key`, `audit-key`, and
`backup-wrapping-key`; connector rows contain only a Keychain reference. This repository
does not provision those entries. Tests use `DeterministicTestKeyProvider`, which derives
isolated test keys from a synthetic seed and cannot resolve connector credentials.

## State and audit invariants

The migration creates dedicated tables for identities, connectors, capabilities, source
items, normalized facts, tasks, events, proposals, approvals, executions, energy check-ins,
goals, pattern snapshots, and consent records. Updates are rejected by database triggers;
transitions append a new version. Deletes require the lifecycle callback.

Every create, transition, deletion, and export requires a nonempty actor and correlation
ID. Audit records contain action metadata and a keyed source fingerprint, never domain
content. Each record is HMAC-linked to its predecessor. Verification runs before trusted
writes and rejects changed sequence, metadata, predecessor, or signature values. Two-year
audit expiry removes only a verified prefix and retains its final hash as a chain anchor.

## Lifecycle defaults

- Raw communications expire after 180 days.
- Raw energy/profile check-ins expire after 365 days.
- Normalized tasks, events, goals, and de-identified pattern snapshots remain until user
  deletion.
- Audit metadata expires after two years through anchored prefix pruning.
- Backup envelope keys expire after 30 days.

Settings may supply shorter communication and health retention intervals. User deletion
purges every stored version, writes only a keyed tombstone, destroys the wrapped data key
for each historical backup containing the record, and creates a clean encrypted backup.
The encrypted historical file is insufficient to restore once its envelope is erased.

## Exports and providers

`StateExporter.redacted` requires transition context and exports only record UUID, kind,
state, and a `content_redacted` marker. It omits payloads and Keychain references and writes
an audit fact. `Provider` is a narrow schema-bound interface. The fake provider performs no
network operation and accepts only exact registered request fixtures.
