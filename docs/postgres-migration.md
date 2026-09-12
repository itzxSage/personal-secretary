# PostgreSQL migration evidence and remaining work

The first cloud persistence slice now runs against a real local PostgreSQL server.
It is not a deployed cloud application and does not migrate the existing personal
SQLCipher database or Google credentials.

## Implemented

- Shared `DomainRecords` / `DomainUnitOfWork` interface, exercised against SQLCipher
  and PostgreSQL for lifecycle, rollback, caught failures, validation and deletion.
- PostgreSQL tenant marker verified before access; one dedicated cell per database.
  A row lock serializes domain writers and audit heads across separate connections.
- AES-GCM encrypted domain, device and outbox content, with tenant/record/version
  associated data. Data and audit keys are independent injected secrets. Managed
  disk encryption remains an additional deployment requirement.
- Audited device enrollment and revocation inside the same transaction as approval
  verification. Revocation is read from the database on each check.
- Atomic signed-approval verification, replay markers, proposal transition, audit,
  and durable Calendar job creation. This is an internal service, not a public route.
- Stable job identifiers, one active claim, expiry, result fencing and reconciliation.
  Expired running claims become uncertain and are never automatically requeued.
- Dispatch revalidates current proposal payload/state and enrolled device before
  returning a claimed operation. It does not invoke Google or issue worker leases.
- Immutable audit rows and durable head verification, including missing-tail checks.
- No schema creation during application connection. Provisioning is a separate
  operation, intended for a migration identity.

SQLCipher domain operations also now roll back on non-driver exceptions, validate
new states before persistence, and reject deleted-record reuse. Its authorization
consumption store no longer commits an enclosing domain transaction unexpectedly.

## Reproduce

Install PostgreSQL binaries (`postgresql@17` on Homebrew), then:

```sh
uv sync --locked --dev
uv run scripts/verify_postgres.py
uv run scripts/verify_lifeos.py --local-only --with-postgres --run-id UNIQUE_RUN_ID
```

The PostgreSQL runner creates an owner-only temporary directory and Unix socket,
disables TCP listening, creates only synthetic schemas, and stops the cluster on
exit. It never starts a login service or uses the owner's default database. The
ordinary Python suite skips database integration tests when no test DSN exists;
that skip is not PostgreSQL evidence. CI explicitly runs the real database checks.

Tests cover independent connections and concurrent writers/approvals/claims,
rollback before enqueue, process exit with an uncommitted transaction, process exit
after a committed claim, revoked-device dispatch rejection, encrypted payload
binding, invalid transitions, and missing audit tails. The synthetic provider
result reference used by reconciliation is not a live Google result.

Transaction behavior follows the documented [Psycopg transaction contexts](https://www.psycopg.org/psycopg3/docs/basic/transactions.html)
and [PostgreSQL row locking](https://www.postgresql.org/docs/17/explicit-locking.html).

## Required before cloud enablement

1. Migrate the canonical conversation journal, goals, memory, learning, leases,
   deletion/export and retention paths; domain parity is not whole-engine parity.
2. Define and test migration checksums/upgrades plus a least-privilege runtime role.
   Current synthetic tests use a schema owner. Triggers do not constrain a database
   administrator, and no database grants are claimed as validated yet.
3. Implement workload identity, KMS unwrapping/rotation and secret management.
   `CloudStateKeys` injection is a testable boundary, not a KMS implementation.
4. Add account-backed device enrollment and authenticated cloud ingress. The local
   Mac relay still uses actual socket mTLS identity; a proxy header cannot replace it.
5. Connect worker leases and the real Calendar executor/reconciler. Recheck authority
   at the execution boundary and define revocation behavior for work already started.
6. Implement backup restoration with deletion/revocation ledgers, audit checkpoints,
   migration rollback, quotas, retention, pooling and load/lock-timeout tests.
7. Deploy synthetic staging, capture Mac-off phone evidence, then migrate explicitly
   selected personal data/connections with recovery and rollback records.

The current whole-cell lock and full audit verification favor simple correctness
for a small dedicated deployment. Benchmark before increasing traffic; they are not
claims of scalable shared multitenancy or bounded latency under a large audit history.

## Dependency provenance

Psycopg and its binary distribution are pinned by `uv.lock` and identify themselves
as LGPL-3.0-only in their installed package metadata. The existing policy therefore
retains review-required warnings; they were not relabeled permissive. Review binary
redistribution, notices and bundled-library obligations before packaging a release.
The Windows-only tzdata dependency declares Apache-2.0 in its
[upstream package](https://pypi.org/project/tzdata/).
