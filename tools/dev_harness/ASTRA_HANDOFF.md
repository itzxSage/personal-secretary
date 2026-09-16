# Astra → free OpenCode engineering team

## Mission and authority

Continue this upgrade; do not redesign or restart it. The user authorized inspection and modification of local OpenCode/Oh My OpenCode configuration and supporting tooling. Preserve uncommitted work, useful configuration and upgradeability. Back up configuration before changing it. No paid model calls, paid provisioning, external deletion, public exposure, secret disclosure, destructive Git operations, or unrelated personal-file edits. Do not commit without authorization. Astra stopped at the user's request to conserve usage; this is a partial implementation handoff, NOT a completion report.

**Primary objective: an intelligence-amplifying engineering harness, not merely a test harness.** Improve cheap agents' ability to UNDERSTAND → PLAN → RETAIN REQUIREMENTS → DECOMPOSE → IMPLEMENT STRONG CODE → INTEGRATE → TEST → VERIFY → REPAIR → REACH THE USER'S ACTUAL OUTCOME → ESCALATE INTELLIGENTLY.

The user wants to provide a normal engineering objective without an orchestration essay. The harness should turn it into durable mission state, reconnoitre the repository, plan dependency-aware vertical slices, implement them, reconcile progress with original requirements, test, independently review integration/outcomes, repair, checkpoint, and escalate only when justified. Components or completed subtasks are not success if the requested vertical does not work. Architecture, difficult debugging, security-sensitive uncertainty and high-risk decisions justify senior escalation; produce a packet for the user, never automatically spend on Astra/Codex.

Observed failure motivating this: a cheap agent said pytest passed, but later senior review found failures. Requested != implemented != tested != verified. Queued prompts are not proof prior waves completed. Simulator behavior does not establish physical behavior. Optimize detectability, recoverability and inexpensive mistakes, not confident-sounding summaries.

## Current state (2026-09-14)

| Area | State | Evidence/qualification |
|---|---|---|
| Installed harness inspection | COMPLETE for initial implementation decisions | Findings below; recheck current files |
| Existing configuration changes | NOT_STARTED | No global/OMO config was modified by Astra |
| Configuration backups | NOT_STARTED | None created because no configuration mutation occurred |
| Python single-contract runner | PARTIAL / UNVERIFIED | `harness.py` exists; syntax and CLI help passed only |
| Constitution | COMPLETE draft / UNVERIFIED integration | `CONSTITUTION.md`; not globally installed |
| Role prompts | COMPLETE draft / UNVERIFIED integration | `roles.json`; runner loads role-specific prompt |
| Routing | COMPLETE draft / UNVERIFIED integration | `routing.json`; only runner consumes it, not OMO |
| Automated objective → plan → slices → outcome lifecycle | NOT_STARTED | Design below; no campaign module exists |
| OMO commands, hooks, compaction bridge, installer | NOT_STARTED | No plugin or command was installed |
| Controlled workflow evaluation | BLOCKED / UNVERIFIED | Synthetic smoke attempted, hit AssertionError; cause not isolated |
| Live free-model evaluation | NOT_STARTED | No models were called by Astra for this work |
| Production readiness | UNVERIFIED | Do not advertise this as an operational upgrade yet |

Only `tools/dev_harness/` was added to the repository. Initial Git status was clean. Last status: `?? tools/`. No commits, resets, stashes or history modifications. Last successful checks: `python3 -m py_compile tools/dev_harness/harness.py`, CLI `--help`, and `opencode debug config` (existing config still resolves). No pytest, lint/type suite, permission-boundary test or integration evaluation passed for this harness. No STABILIZATION.json was produced: the attempted synthetic script asserted before its final write. Do not upgrade this claim based on this handoff.

The interrupted synthetic evaluation used a temporary Git repo with `value.txt`, a real Python subprocess asserting its content, and injected fake builder/verifier callbacks through `run(..., invoke=...)`. It intended four cases: lying builder/reviewer plus failing command → two failures/BLOCKED; targeted debugger repair receives `VALUE_BROKEN` → VERIFIED; physical gate → PHYSICAL_VERIFICATION_REQUIRED; successful slice → VERIFIED. It failed an assertion without isolation. A diagnostic follow-up was interrupted. Reproduce with per-case diagnostics; verify fixture Git commit succeeds (global Git settings may affect temporary fixtures). No real project source was changed by these fixtures.

## Actual installation findings

- Workspace: `/Users/jrsgagne/Development/personal-secretary`.
- OpenCode executable: `/Users/jrsgagne/.npm-global/bin/opencode`; version **1.18.30**.
- Global config: `~/.config/opencode/opencode.jsonc`. Contains `oh-my-openagent@4.19.4`, LSP enabled, compaction auto/prune true. `tui.json` also loads OMO 4.19.4. Preserve both.
- Actual OMO configuration: **`~/.omo/omo.jsonc`, under `[opencode]`**. Do not mistakenly create/overwrite legacy `oh-my-opencode.json`.
- Installed OMO source/schema evidence: `~/.cache/opencode/packages/oh-my-openagent@4.19.4/node_modules/oh-my-openagent/dist/index.js` and `dist/tui.js`.
- Plugin SDK: `~/.config/opencode/node_modules/@opencode-ai/plugin`, **1.18.26**. `dist/index.d.ts` declares tool hooks, custom tools, system transform, `experimental.session.compacting` (append `context`, don't replace full prompt), text-complete and tool-before/after hooks. `dist/tool.d.ts` supplies trusted `context.agent`, `sessionID`, `directory`, `worktree`, abort signal. Validate against actual runtime before depending on these.
- OMO schema supports `prompt_append`, model/fallback overrides, role permissions, `compaction.model`, `ultrawork.model`; Hephaestus has `allow_non_gpt_model`. Preserve supported extension approach, no upstream fork.
- Resolved default: `Sisyphus - ultraworker`. Sisyphus ~36K prompt chars, Atlas ~27K, Hephaestus ~20K, Junior ~20K. Avoid adding another giant permanent prompt.
- All inspected OMO primary agent/category assignments were paid-first: Sisyphus/Hephaestus/build/Oracle Sol; planning/Atlas/Junior mostly Terra; exploration/librarian Luna-fast. Free models existed in fallback lists. UI selection can override current primary, but does NOT guarantee all delegated roles are free.
- Team mode/tmux enabled, window isolation, background default concurrency 3, provider concurrency OpenAI 2/OpenCode 2. Runtime fallback enabled, max attempts 3, cooldown 300s, **timeout_seconds 0** (unbounded timeout configuration).
- Resolved global permissions included webfetch allow, external_directory allow, task deny; OMO roles override task permissions and provide task/teammate orchestration. Read-only roles deny edit/write but are not an OS sandbox.
- MCP names: websearch, context7, grep_app, lsp, codegraph. Do not print their credential-bearing config or auth stores.
- Existing commands: playwright, frontend, git-master, review-work, remove-ai-slops, init-deep, debugging, security-research, security-review, visual-qa, team-mode, goal, refactor, start-work, stop-continuation, handoff, hyperplan.
- `opencode debug skill`: built-in customize-opencode, cached security-research/security-review. No applicable AGENTS.md was found in this repo or inspected parent locations.
- Important upstream conflict: installed Atlas prompt says "There is no retry cap" and to keep iterating. Todo-continuation and compaction context/todo hooks exist. A two-strike policy needs a real controller boundary; appending prose alone may conflict with upstream continuation.
- Read-only resolved config snapshots were captured under `/tmp/lifeos-opencode-resolved.json` and `/tmp/lifeos-opencode-final-resolved.json`. Treat as local potentially sensitive diagnostic files, never commit/copy wholesale.

Available via `opencode models`: opencode/nemotron-3-ultra-free, mimo-v2.5-free, nemotron-3.5-lightning-free, ling-3.0-flash-fin-free, muse-spark-1.2-contributor-free, muse-spark-1.3-contributor-free, big-pickle; OpenAI includes gpt-6-astra and multiple Sol/Terra/Luna models. Availability listing is not proof inference works or pricing is permanently unchanged. Use explicit `-free` IDs for automated execution; no silent paid fallback.

Existing LifeOS tooling worth preserving/reusing:
- `scripts/verification.py`: subprocess checks, source digest, result files.
- `scripts/verify_lifeos.py`: fixed local checks including pytest JUnit, secrets, types, format, integration/release gaps.
- `scripts/verify-foundation.sh`: Python/Swift/contracts/dry-run checks, fail-fast shell.
- `scripts/scan_secrets.py`, existing CI `.github/workflows/`, `pyproject.toml` (strict Ruff and basedpyright).
- `.gitignore` already ignores artifacts, .omo, private bootstrap patterns and common secrets.
These support evidence, but an agent can still skip them and assert completion. Preserve them rather than duplicate their domain checks or weaken existing gates.

## Files and current runner design

`harness.py`: standalone stdlib CLI `run CONTRACT.json --repo PATH` / `status EVIDENCE_DIR`. State defaults to `~/.local/state/lifeos-harness/<timestamp-uuid>/` (outside Git); directory mode 0700. JSON writes via temporary file replacement. Contract is copied to evidence. No arbitrary "set VERIFIED" CLI.

Contract presently requires objective, unchanged[], acceptance[], negative_cases[], files[] (exact paths), checks[] (`kind: command|pytest`, argv array, optional timeout_seconds), gates[], dependencies[] (prior status file paths); optional risk normal/high/security and context[]. Current model plan generation is absent.

Runner captures Git tracked/nonignored-untracked file hashes/modes and HEAD, baseline checks, then at most two implementation/review rounds. Builder and debugger use Nemotron, independent verifier MiMo in separate `opencode run --pure` processes. `OPENCODE_CONFIG_CONTENT` supplies a custom harness-worker agent with step cap 24, explicit free model/small_model, opencode-only providers, shell/delegation denied, role edit allowlist and read restrictions. This **has not been validated in the installed runtime**. Pure processes deliberately avoid OMO retry/continuation/fallback machinery for the controlled worker boundary; retain OMO as user-facing coordinator.

Python owns test subprocesses (argv, exit code, combined output, start timestamp, duration, timeout kills process group). Pytest appends fresh JUnit path and requires nonzero tests, zero errors/failures/skips. Model prose cannot override failed checks. Verifier JSON must explicitly cover each acceptance string. Source changes during verification block success. Builder edits outside declared files block. One repair receives prior command/review evidence. Two failed rounds produce BLOCKED and handoff. High/security risk blocks before implementation without paid calls. Physical gates prevent automatic VERIFIED; no human attestation mechanism exists yet. Diff, status, event transitions and handoff are persisted.

`CONSTITUTION.md`: concise authority, evidence, safety, physical boundary and compaction invariants.
`roles.json`: planner, plan reviewer, builder, debugger, verifier, integration reviewer quality prompts. Only builder/debugger/verifier are invoked today.
`routing.json`: proposed free role selections; not an OMO configuration.

## Important defects/risks to resolve, not hide

1. Synthetic smoke failed; diagnose before extending architecture.
2. Validator is incomplete (types, empty strings, bounded sizes/timeouts, malicious/ambiguous paths). Fail closed on malformed model output. Record interruption/process failure as durable BLOCKED with useful packet.
3. **Arbitrary contract argv is trusted code execution**, even with `shell=False`. Do not execute model-generated shell/Python commands automatically. Add a reviewed command registry/project profile; planner selects check IDs and constrained existing test targets. Manual trusted contracts are a separate explicit trust boundary. Tests themselves can execute code; this is not an adversarial OS sandbox.
4. Existing blanket read/permissions, merged config, custom tools and provider restrictions need actual tests. Secrets can live under unrecognized filenames; filename filters are not DLP. No grep permission was granted to workers to avoid bypassing read filters.
5. Baseline failures are evidence but currently do not prevent unrelated implementation. Require explicit repair scope for broken prerequisites; distinguish expected red acceptance tests from unrelated broken foundations.
6. Dependency equality currently compares the entire repo snapshot, too strict for multi-slice chains. A campaign controller should track accepted checkpoints plus descendant changes/reverification. Never treat old full-repo VERIFIED as valid after arbitrary changes.
7. Status is local same-user mutable files, not cryptographic tamper protection. A shell-enabled external agent can overwrite them. Worker denial reduces accidental self-certification, not hostile same-user tampering. No global stop gate exists.
8. `git diff HEAD` omits untracked file contents; snapshot hashes include them. Reviewer must read them; improve packets to list untracked paths and bounded patches safely. Ignored files are outside snapshot coverage. Concurrent runs need a per-repo lock; avoid silently discarding changes.
9. Current handoff packs large outputs, then truncates tails in prompts. Preserve critical failure summaries/counts structurally, with full logs by reference. No compaction hook or resume command exists.
10. No authentic physical/human observation ingestion. Keep automatic status physical-required; do not implement a builder-accessible self-attest loophole.
11. Security risk classification currently trusts contract `risk`; no semantic detector can guarantee classification. Add independent plan review and conservative explicit escalation triggers.
12. Harness files have not been Ruff-formatted/linted; repo uses ALL rules. Fix your additions rather than weakening repository quality gates. Do not claim whole LifeOS test suite passes based on harness fixtures.

## Dependency-ordered continuation plan

### 1. Stabilize existing runner and prove its boundaries
Reproduce synthetic failure with readable per-case diagnostics. Add meaningful isolated automated tests for transitions, process failure, stale evidence, invalid JSON, path escape, immutable acceptance, skipped/missing JUnit and two-strike repair. Fix real defects. Capture actual exit results. Keep scope here until foundations work.

### 2. Durable mission and safe planning layer
Add objective-first command/controller, separate from single-slice runner. Persist the user's exact objective verbatim plus requirement IDs, non-goals, constraints, dependencies, acceptance, uncertainty and physical/human gates. Planner performs read-only repository reconnaissance, cites actual architecture/files and proposes at most a few vertical slices. Independent plan reviewer checks objective coverage and runtime path. Unresolvable material ambiguity → concise question; no invented requirement. Safe check registry must precede automatic plan execution.

Mission design: immutable original request + versioned contract/plan; each slice maps requirement IDs, predecessor IDs, exact edit files, relevant context paths, focused checks, runtime integration path and acceptance. Final outcome checks map EVERY required outcome. Deterministically reject cycles, unknown prerequisites, uncovered requirement IDs, missing final checks, undeclared files and attempts to silently mutate contract. These shape checks do not replace semantic independent review.

### 3. Controller / plan conformance / outcome execution
Sequential bounded slices first; avoid parallel implementation sharing a worktree. Before each slice verify prerequisite status, current source and original mission. Build small context packet: mission constraints/outcomes, current slice, architecture references, relevant source/tests, current diff, dependency state and concise prior failure. Persist packet; fresh context per role. Reconcile every slice against original mission, update requirement ledger from machine/reviewer evidence only. A failed foundational slice blocks descendants. Final all-slice integration review/tests are mandatory even if all subtasks passed. Trace actual entry -> config -> runtime consumer -> state/output, including failure paths.

### 4. Repair / checkpoints / escalation
Failure → structured evidence → diagnosis/hypothesis → minimal repair → focused retest → full applicable independent verification. Two substantive failed verification rounds then STOP; distinguish infrastructure failure from code failure and trivial deterministic fixes without permitting endless retries. Security uncertainty escalates immediately. Packet includes exact objective, architecture, approaches, changes/diff, failures/counts/log links, hypotheses, likely files and smallest unresolved question. Emit compact packet and wait for user/senior outside paid automatic routing. Physical-ready must remain separate from observed success.

### 5. Supported OMO integration and free routing
Back up actual configs. Preserve TUI/team/MCP/commands. Change ordinary agent/category routing to verified available free models; remove paid auto fallback and bound runtime retries/timeouts. Recommended roles: Nemotron Ultra builder/debugger/plan critic; MiMo planner/verifier/final integration reviewer; Lightning explorer. Distinct verification model/context is required. These are hypotheses to evaluate, not benchmark claims. Senior Astra is manual packet consumer only.

Use supported append prompts, small permanent constitution, command/custom tool and compaction hook where proven. OMO front end hands objective to controller; controlled workers use bounded pure processes. A concise `/engineer desired outcome` or custom tool should establish mission without huge prompts. Consider job start/status tools so long work does not block the parent; prove concurrency and cancellation. Compaction appends durable mission ID, original outcome, current checkpoint, unresolved dependencies/gates and packet path; re-read state after compaction. Do not replace useful upstream summaries. Reconcile "no retry cap" behavior through controller-enforced boundary, not ceremonial instructions.

### 6. End-to-end controlled evaluation and operational docs
Use disposable repo fixture with an actual runtime entry, implementation and tests. Prove live Nemotron can implement and live MiMo independently review under real permissions. Intentionally start with disconnected wiring and a failing negative case: green component tests alone must not grant outcome success. Demonstrate requirement retention over dependent slices/fresh contexts, blocked prerequisites, drift detection, failure evidence reaching debugger, successful repair, second substantive failure escalation, stale source rejection, false completion rejection, nonzero failing pytest exit plus exact JUnit counts, skipped/zero-tests rejection, physical self-certification refusal, and usable compact handoff. Separately test hooks/commands in actual installed OpenCode. No paid services or real private device/account actions needed for fixture.

Document exact setup, normal/high-risk task, manual senior escalation, model updates, evidence locations, physical gate and limitations. Final report must distinguish machine-enforced vs orchestration vs prompt vs routing improvements, with exact observed results. Do not claim the harness operational merely because config parses.

## Continuation instructions

Read this entire file and inspect current state. Preserve Astra's code and decisions where useful; repair evidenced defects instead of starting over. Follow dependency order. Use free models only. Do not let existing OMO paid-first roles sneak into delegation: verify/adjust routing before invoking those roles. The user authorized continuation and configuration upgrades; do not ask permission again for reversible scoped work. Ask only for genuinely missing decisions or explicit external/physical gates. Keep communication concise. Continue implementing until the upgrade is operational and honestly evaluated, or stop with a compact evidence-backed blocker.
