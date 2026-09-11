# Staging conversation relay

The relay now persists text conversation events and supports signed delivery,
acknowledgment and cursor resume. It does **not** interpret messages, generate
assistant replies, execute tools or connect realtime audio. Production remains
disabled. Use synthetic staging conversations until the remaining privacy and
native-delivery requirements below are completed.

## Security and persistence

- Uvicorn requires a client certificate from the configured CA. The HTTP/1.1
  protocol adapter captures SHA256 of the peer's DER SubjectPublicKeyInfo directly
  from the TLS session. An HTTP header cannot supply that identity. Proxy-header
  handling and WebSocket upgrades are disabled.
- Every request additionally carries an Ed25519 signature from its currently
  enrolled device. Revocation is re-read from encrypted state on every request.
- Request IDs are UUIDs consumed durably; rejected signed application requests
  also consume their nonce. Retries use a fresh request ID and timestamp, but the
  original conversation/event IDs and content. Signatures cannot authorize a
  proposal because their domain differs from approval signatures.
- Membership comes from a local device-to-canonical-participant binding. The
  remote client cannot enroll devices, choose arbitrary membership, or submit
  tool results. Only transcript partial/final, cancellation and resume events are
  currently accepted from clients.
- Event content lives in append-only canonical SQLCipher records. A transaction
  holds the sequence/index and content-minimized HMAC audit updates together.
  Invalid batches roll back completely; exact event retries return an ACK without
  duplicating content or audit. Cancellation state is reconstructed after restart.
- Requests are limited to 256 KiB/10 seconds, batches to 100 events, events to
  16 KiB encoded, pages to 100 events, and streams to 10,000 events. At that limit
  start another conversation. The single-process staging server limits concurrent
  connections to 16 and does not log request bodies or access URLs.

## Wire format

Headers: `X-LifeOS-Contract-Version: 1.0.0`, `X-LifeOS-Device`,
`X-LifeOS-Request-ID`, `X-LifeOS-Issued-At` (Unix seconds), and
`X-LifeOS-Signature` (hex). Sign these ASCII fields separated by a single newline,
with no trailing newline:

```text
lifeos.request.v1
METHOD
/exact/path?exact=query
lowercase-device-uuid
lowercase-request-uuid
decimal-issued-at
lowercase-sha256-of-raw-body
```

Requests may be at most 60 seconds old and 5 seconds ahead of the server. A GET
signs the digest of an empty body. Both languages test common signing bytes and
verify a signature produced by the other implementation.

| Endpoint | Body or result |
| --- | --- |
| `POST /v1/conversations` | `{ "conversation_id": "UUID", "title": "Secretary" }`; returns the shared Conversation model |
| `POST /v1/conversations/UUID/events` | `{ "events": [ConversationEvent, ...] }`; returns `acknowledged_event_ids` and `next_sequence` |
| `GET /v1/conversations/UUID/events?after=0&limit=100` | Returns `events` and the last returned sequence as `cursor` |

`401` means failed device authentication; `403` means unavailable membership;
`409` means content/ordering conflict; `413` means oversized input; `422` means
invalid JSON/model; `426` means incompatible contract. Error responses omit
submitted content. Sequence conflicts are never silently renumbered.

## Explicit Mac setup

Nothing is configured automatically. Existing SQLCipher/audit keys must already
be available through `MacOSKeychainKeyProvider`. Obtain a server certificate with
the correct hostname, a dedicated client CA and a device client identity. Keep
TLS private-key files private. The server must trust the client CA; the client
must trust the server certificate through normal system trust.

The device also needs its own Ed25519 signing key. Export **only its public key**
as 64 hex characters for local enrollment. Never use public fixture signing keys.
Use stable, newly allocated UUIDs for this staging setup, not demo identities.

```bash
uv run scripts/relay.py --state /absolute/staging.sqlite provision \
  --device-id DEVICE_UUID --participant-id PARTICIPANT_UUID --name 'Staging phone' \
  --client-cert /absolute/client.pem --signing-public-key /absolute/device-public.hex

uv run scripts/relay.py --state /absolute/staging.sqlite serve \
  --server-cert /absolute/server.pem --server-key /absolute/server-key.pem \
  --client-ca /absolute/client-ca.pem
```

The default bind is `127.0.0.1:8443`. A deliberate `--host` override is necessary
for a paired device; keep staging on a private network. Do not expose the fixture
calendar API as a substitute. The custom Uvicorn protocol adapter is exercised by
real TLS tests; rerun those tests when updating Uvicorn.

## Opt-in Swift setup

The app reads `Secretary/relay.json` beneath its own Application Support directory.
No file means local-demo mode and no network calls. The file contains metadata:

```json
{
  "origin": "https://mac-hostname:8443",
  "conversationID": "NEW_CONVERSATION_UUID",
  "participantID": "ENROLLED_PARTICIPANT_UUID",
  "deviceID": "ENROLLED_DEVICE_UUID",
  "signingKeyAccount": "staging-request-signing",
  "clientPKCS12Account": "staging-client-pkcs12",
  "clientPKCS12PasswordAccount": "staging-client-pkcs12-password",
  "serverCertificateSHA256": "PINNED_SERVER_LEAF_SHA256"
}
```

The raw 32-byte device signing key must be in the app-accessible Keychain generic
password item with service `com.secretary.relay` and the configured account. Use
WhenUnlockedThisDeviceOnly protection. The encrypted PKCS#12 client identity and
its password use the same service and their configured accounts. The app imports
the identity in memory, supplies its certificate chain for mTLS, validates the
private CA and pins the exact server leaf. An interactive pairing UI is still
needed; these are developer staging prerequisites, not a finished installation.

For a paired debug device, `scripts/setup_private_relay.py` creates a fresh 30-day
certificate set, request-signing key and identifiers in an owner-only directory.
`scripts/pair_ios_relay.py` passes the material through a one-shot USB launch and
stores it with device-only Keychain protection; no secrets enter the repository or
app bundle. A subsequent normal launch has no pairing values in its environment.

Configured text sends save to the encrypted outbox first, then synchronize. A
Sync button retries explicitly. URLSession uses private-anchor evaluation plus an
exact leaf pin, ephemeral storage, bounded responses and no redirects. Only a valid full ACK
removes queued events. Encrypted delivery cursors are saved before removal, so a
crash leaves retryable duplicates rather than a forgotten sequence. Existing demo
messages are never relabeled or uploaded under a new setup. A configuration
change with an incompatible pending queue requires explicit resolution.

The app accurately reports delivery to the Mac, not an assistant response.
Delivered history is fetched during synchronization and cached with AES-GCM for
offline display. Server-confirmed deletion purges that cache and the matching
outbox records; a failed deletion deliberately retains both. Stale cross-device
sequences preserve the pending queue and show a conflict; editing/reconciliation
UX is outstanding.

## Verification and remaining gates

```bash
uv run pytest -q tests/test_conversation_relay.py tests/test_relay_https.py
swift test --package-path ios/SecretaryApp
```

TLS tests generate an ephemeral CA, run a real loopback HTTPS server and clean up
their temporary state. They test valid delivery, missing and unbound client
certificates, revocation, signature/body tampering, batch limits and header
spoofing. No personal accounts, Keychain provisioning or live provider calls are
involved. Shared Swift tests cover queue preservation, ACK validation, retry
nonces, encrypted restart cursors and cross-language signatures. A real iPhone
connection and native TLS identity import have **not** been verified here.

Conversation content now has signed deletion, a fixed 180-day expiry deadline,
an explicit `relay.py prune` command, and encrypted offline delivered-history caching.
Before release: schedule expiry, integrate backup invalidation, expire nonce records,
add incremental journal checkpoints, pairing/conflict UI, WebSocket/audio and real
assistant/provider wiring.
Full Xcode, simulator and paired-device tests remain required. Local transport
tests do not satisfy the complete release transport gate.
