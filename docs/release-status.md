# Release status

The local engine and fixture workflows are executable. The complete personal
assistant is not ready to deploy. The old plan's checked boxes are historical
implementation claims; current verification establishes what is tested.

## Codex continuation

- Signed approvals now bind the exact action, payload, device, actor, timestamps
  and idempotency identity. Claiming a known device ID without its private key fails.
- Encrypted enrollment retains keys/revocations across restarts. Calendar and
  rollout replay markers persist with atomic multi-key claims.
- The HTTP demo rejects forged approval with 403 before legitimate signed approval.
- New traceability, secret-pattern scan, policy audit, aggregate E2E and release
  commands record source hashes, command outcomes and log paths.
- Simulator requests no longer pass after running fixture tests alone.
- Added a real consent-gated Responses API text interpreter and a proposal-only
  CLI. Its request, response and security contracts pass local mocked-provider
  tests; live validation and app wiring remain outstanding. See
  [provider interpretation](provider-interpretation.md).
- Persistent conversation ingress now verifies actual mTLS peer fingerprints and
  Ed25519 request signatures, rejects durable nonce replays, and atomically commits
  canonical events, sequence indexes and minimized audit. Real loopback HTTPS tests
  cover missing/wrong certificates, revocation, delivery retries and resume.
- Swift signed delivery, pinned private-CA mTLS, redirect refusal, encrypted delivery
  cursors and opt-in app synchronization compile and pass shared-client tests.
  On 2026-09-11, a fresh staging identity was installed in the physical iPhone's
  device-only Keychain and a real phone-to-Mac create/append/read/delete cycle
  succeeded over the private LAN. The 12 smoke events were then purged from both
  sides. [Relay setup](conversation-relay.md) remains developer-only and explicit.
- Signed conversation deletion, 180-day expiry, encrypted offline delivered history,
  and an explicit retention-prune command now have restart and real-HTTPS coverage.
- A real fixed-host Google Calendar REST transport and installed-app OAuth bootstrap
  now enforce the app-created-only scope, Keychain credential/ID custody, deterministic
  insert recovery, incremental sync and ETag conflict refusal. On 2026-09-08, the
  user authorized the narrow scope and a live token refresh plus dedicated-calendar
  create/readback succeeded on this host without writing an event. Live event
  lifecycle, revocation and service-wiring evidence remain outstanding. See
  [calendar setup](google-calendar-live.md).

## Remaining release work

1. Extend the implemented persistent HTTPS text relay to realtime WebSocket/audio,
   microphone/playback and assistant replies. Replace developer USB pairing with native UI,
   automatic retention scheduling, backup invalidation, nonce expiry, incremental
   journal checkpoints and conflict resolution UX; validate iPhone-to-Mac on a device.
2. Wire and live-test the new OpenAI text interpreter and the Google Calendar event
   lifecycle; implement real realtime and OpenClaw sidecar/channel transports; test
   with configured sandbox accounts and provider consent/retention settings. Existing
   E2E flows still use fixtures.
3. Complete the native UI/voice and widget-extension harness. Xcode 26.3 now builds
   and tests the app on the iOS 26.3 Simulator, and development-signs, installs and
   launches it on the paired iPhone. A physical Push to Talk receipt has been
   observed, and private-LAN signed relay delivery plus deletion now have physical
   device evidence. End-to-end audio and remaining system surfaces still require evidence.
4. Extend durable authority to every voice/lease path and prove crash recovery
   across authorization, state transitions and external execution.
5. After those checks pass, present the reversible-calendar-only promotion for
   explicit authorization, as required by the approved plan's F5.

`release-requirements.json` names the required evidence locations. Creating report
files or changing checkboxes does not implement these features: each acceptance
report must reference reviewable execution evidence for the current source.

## Run verification

```bash
uv run scripts/verify_lifeos.py --run-id review-001
uv run scripts/audit_humane_use.py fixtures/authority fixtures/voice fixtures/energy
uv run scripts/scan_secrets.py
bash scripts/e2e_lifeos.sh --ios-simulator 'iPhone 15' --run-id native-review
```

Use a fresh aggregate run ID to preserve previous evidence. The release command
continues independent checks and returns nonzero while release requirements are
unmet. Fixture E2E without `--ios-simulator` can pass independently. The scanner
prints rule/path/line findings, never credential values; its pattern coverage is
not a comprehensive secret audit. Production capabilities remain disabled.

CI runs the same verifier with `--local-only`, which can pass when all local
checks pass while retaining `release_ready: false`. The reviewed prior plan is
preserved in `lifeos-plan.md` so verification does not depend on a home-directory
OpenCode installation. Native and live-provider evidence must still be produced.
