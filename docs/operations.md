# Mac Service Operations

This is the local development/staging runbook. Production must run independently
of a personal Mac; see [cloud architecture](cloud-architecture.md). Mac Keychain,
launchd and installed-app OAuth are development integrations, not the production
deployment contract.

## Local dry run

```bash
uv sync --dev
SECRETARY_DRY_RUN=1 mac/secretary_service/bin/run
```

The launcher binds to `127.0.0.1:8765`, disables connectors, and refuses non-dry-run mode.

## Dedicated runtime account

Create a hidden, non-login `_secretary` account with an unused system UID according to
your organization’s macOS account policy. Do not reuse a personal login or grant connector
permissions during Todo 1. Confirm it exists with `id _secretary`, then install:

```bash
sudo infra/launchd/install.sh
```

The LaunchDaemon runs as `_secretary`, starts at load, and restarts after unsuccessful
exit. The checked-in plist is a template; installation substitutes the absolute checkout
path. Use `sudo launchctl bootout system/com.personal-secretary.service` to stop it.

No credentials belong in the plist, repository, environment dumps, or verification files.

## State keys

Production state expects 32-byte base64 values at macOS Keychain service
`com.personal-secretary.service` under accounts `database-key`, `audit-key`, and
`backup-wrapping-key`. Provisioning and rotation are intentionally outside Todo 2. The
service must fail closed rather than create a replacement key when a reference is absent,
locked, malformed, or lost.

## Task 18 rollout and recovery

See `privacy-export-revocation.md` for backup eligibility, database-key loss, export, and
revocation order. See `upstream-update-playbook.md` for the sandbox-only
dry-run -> proposal -> approved reversible-apply -> rollback sequence. Neither procedure
enables production fabric, workers, connectors, or executors.
