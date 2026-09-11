# Real text interpretation adapter

`slice/openai_interpreter.py` implements the existing `InterpretationAdapter`
interface using the OpenAI Responses API over TLS. It is available for real
requests but is not enabled in the fixture server. It has been tested with
controlled provider responses; live account validation remains outstanding.

The implementation follows the official [Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs)
and [Responses API reference](https://developers.openai.com/api/reference/python/resources/responses/methods/create).
It sends `text.format` with a strict JSON schema and `store: false`, exposes no
tools, and constructs proposal/source IDs locally. Deterministic planner code
computes the schedule. Protected commitments, planning windows, revision and
baseline metadata cannot be changed by provider output.

Requests require active text-interpretation consent and current project retention
verification before any Keychain lookup or network request. `store: false` alone
is not proof of project-level retention controls. Retention settings must reflect
actual verified project configuration, not values invented to enable the adapter.
The API key is read by opaque reference from the existing macOS Keychain service
`com.personal-secretary.service`. No key is read during fixture verification.

## Run a proposal

Prepare a `DayPlanRequest` JSON snapshot of the intended day and a private local
configuration containing `model`, `project_id`, `key_reference`, `consent_until`,
and `retention` (the existing `RetentionVerification` schema). Model/project
selection is explicit; there is no default billable provider activation. Keep
the configuration outside source control, and do not put key values in it.

```bash
uv run scripts/interpret_plan.py --config /path/to/private-config.json \
  --base-plan /path/to/day-plan-request.json
```

Provide the request on stdin (finish with EOF). Output is a proposed schedule
only: no calendar apply, state write, worker dispatch or approval is performed.
It contains personal plan content, so only redirect it to a private destination.

The transport uses a fixed official host, TLS verification, a timeout, bounded
request/response sizes, no redirects and no automatic retries. Failures are
content-redacted. Tests cover withdrawn consent, provider refusal/incomplete
output, invalid planner data and changed protected/canonical constraints.

This completes the text interpretation seam, not realtime audio, Google OAuth,
the persistent Conversation API or iPhone integration. Those remain separate
requirements in the release report.
