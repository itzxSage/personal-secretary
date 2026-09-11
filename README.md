# Personal Secretary / LifeOS

A private Life Engine with encrypted local state, deterministic scheduling,
goals and memory, proposed calendar changes, voice/conversation contracts,
a Swift client shell, and constrained OpenClaw/worker adapters.

The local engine and fixture scenarios are executable. This is **not yet an
operational iPhone-to-cloud personal assistant**. A persistent mTLS text-event
relay and opt-in Swift delivery now exist, and physical iPhone-to-Mac text delivery
and deletion have been verified. Cloud operation and realtime audio remain unfinished.
See [capability maturity](docs/capability-maturity.md),
[cloud architecture](docs/cloud-architecture.md) and [release status](docs/release-status.md).

## Run locally

Requirements: macOS, Swift 6.1+, uv, and curl. Full Xcode and a paired iPhone are
additionally needed for native iOS QA.

```bash
uv sync --locked --dev
uv run pytest -q
swift test --package-path ios/SecretaryApp
bash scripts/e2e_lifeos.sh --run-id local-demo
```

The fixture demo exercises signed calendar approval over local HTTP, forged
approval rejection, rollback, voice interruption/resume, recovery and Swift
checks. It does not contact personal accounts.

A real, proposal-only OpenAI text interpreter is now available separately.
See [provider setup](docs/provider-interpretation.md) for its consent, project
retention and Keychain prerequisites. It is not enabled in the fixture server.

The [staging conversation relay](docs/conversation-relay.md) supports encrypted,
authenticated text delivery and resume. Tests exercise real loopback mTLS without
personal credentials. Developer USB pairing works; native onboarding and realtime
voice remain unfinished.

An installable iOS Xcode project can be regenerated from
`ios/SecretaryApp/project.yml` with `xcodegen generate --spec
ios/SecretaryApp/project.yml`. The local development team's signing configuration
is present; other developers must select their own team in Xcode.

The real least-scope Google Calendar REST/OAuth path is also implemented and
disabled by default. See [calendar staging setup](docs/google-calendar-live.md).

For complete evidence, use a fresh run ID:

```bash
uv run scripts/verify_lifeos.py --run-id review-001
```

Reports are in `artifacts/verification/<run-id>/`. The full verifier returns
nonzero while release requirements are unmet, even when local checks pass.
Use `--local-only` for CI's local checks; it leaves `release_ready: false` in the
report while the documented release requirements remain unmet. The reviewed
plan is preserved at `docs/lifeos-plan.md`; pass `--plan` to audit another copy.

## Layout

- `mac/secretary_service/`: encrypted engine, policy, planners and adapters.
- `ios/SecretaryApp/`: Swift contracts, offline outbox and app shell.
- `contracts/`: versioned schemas and generated language bindings.
- `fixtures/`: synthetic calendar, voice, OpenClaw and recovery scenarios.
- `scripts/`: local execution and evidence-producing verification.
- `docs/`: architecture, operations, authority and release requirements.

The health-only service is `secretary_service.app:app`. The separate
`secretary_service.slice.api:app` is a fixture-backed calendar demo. Neither
should be exposed publicly. Read [operations](docs/operations.md) before any
launchd installation. Production capabilities remain disabled.
