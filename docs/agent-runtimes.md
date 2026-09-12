# Replaceable agent boundaries: OpenClaw and Hermes

The owner requested Hermes as an additional integration because of concerns about
OpenClaw updates. We have not established that Hermes is more reliable or that every
OpenClaw update breaks integrations. The architectural response is optional, pinned
runtimes whose failures cannot take ownership of LifeOS state or authority.

| Boundary | Current role | Evidence |
| --- | --- | --- |
| OpenClaw | Inbound channel adapter with enrolled identities and conversation bindings | Existing local contracts and adversarial tests; actual sidecar still outstanding |
| Hermes Agent | Optional outbound planning interpreter using the existing `InterpretationAdapter` interface | Real local HTTP fixture checks; actual Hermes runtime and sandbox not yet deployed |
| Direct model interpreter | Existing consent-gated planning interpreter | Existing provider contract tests; live app wiring outstanding |

Hermes is not a transparent replacement for OpenClaw channel sessions. Channel
bindings, approvals and history remain LifeOS-owned. Adapters can be selected
explicitly at composition time. There is no automatic cross-agent retry, failover
of external actions, or import of OpenClaw/Hermes memories, skills or credentials.

## Hermes implementation

Candidate: `v2026.9.7`, commit `2237be355906fbe6065ce1815711eee52b2d646e`, resolved
from the upstream release tag on 2026-09-11. This is a pinned integration candidate,
not a certification of that release's security or reliability. The manifest is
`infra/hermes/runtime.json`; `uv run scripts/verify_hermes_pin.py` rejects drift.

The adapter uses only three endpoints: authenticated capability discovery, toolset
discovery, and non-streaming chat completion. The endpoint envelopes were checked
against [the pinned upstream source](https://github.com/NousResearch/hermes-agent/blob/2237be355906fbe6065ce1815711eee52b2d646e/gateway/platforms/api_server.py).
Hermes documents its API and execution behavior in the
[API server guide](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server).

Before sending planning input, LifeOS requires current consent, a trusted deployment
review matching the exact commit and deployment, authenticated capability discovery,
and no enabled toolsets. A review record alone does not prove isolation: the trusted
deployment operator must supply evidence for the actual runtime's network policy,
disabled tools, temporary storage disposal and underlying model retention.

Returned text must validate as a day-plan proposal. Canonical metadata, protected
activities and authorization to recover missed deadlines remain protected by shared
validation across Hermes and the direct interpreter. Agent text, progress, tool-call
output, runtime metadata and claimed approvals cannot execute actions.

The HTTP client uses a configured origin, verified TLS for remote endpoints, explicit
loopback-only HTTP for a local sandbox, bounded input/output and timeouts. Redirects,
arbitrary paths, sessions/admin APIs and automatic retries are disabled. Errors omit
response bodies, prompts and bearer credentials. Fresh opaque request/session IDs
avoid inheriting another interpretation's runtime session.

## Required isolation and rollout evidence

Hermes may execute tools server-side and retain agent state. A prompt telling it to
avoid tools, an empty client tool list, or `store:false` is not an isolation boundary.
The first live deployment must use a separate process/container and isolated identity,
no host-home mounts, no canonical DB access, no LifeOS keys/provider-write credentials,
restricted egress and disposable storage. Disable persistent memory/skill learning,
automatic updates and tool execution in the actual pinned runtime configuration.
Validate these controls with live negative tests before recording a runtime review.

Upgrade procedure for either agent: fetch an explicit candidate, verify provenance,
build an immutable artifact, run adapter/negative tests, run a synthetic canary,
then switch new requests to the approved artifact. Retain the previous artifact for
rollback. A timeout during external work requires durable reconciliation, not a
retry through another agent. Never select an agent based on instructions embedded
in a user document or agent response.

Next Hermes evidence: run the pinned runtime with a synthetic model backend and
enforced tool/network isolation; prove its advertised contract, teardown, cancellation,
retention and upgrade rollback. No live Hermes model request has been made here.
