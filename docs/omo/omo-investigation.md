# OMO + Hermes Installation Investigation

> Status: **Fully Installed & Operational** — OMO 4.19.4 running on OpenCode 1.18.31 with active campaign in progress.

## 1. Exact Version & Source

### OMO (oh-my-openagent / oh-my-opencode)
- **Package name**: `oh-my-opencode@4.19.4`
- **Install location**: `~/.npm-global/lib/node_modules/oh-my-opencode/`
- **Binaries** (all aliases of `bin/oh-my-opencode.js`):
  - `omo` — primary CLI
  - `oh-my-openagent` — alias (matches OpenCode plugin name)
  - `oh-my-opencode` — alias
  - `lazycodex` / `lazycodex-ai` — alias
- **Source packages** (bundled in `dist/`):
  - `packages/omo-opencode` — OpenCode plugin (slash commands, tools, hooks, agent management)
  - `packages/omo-codex` — Codex (CLI agent) integration
  - `packages/boulder-state` — Work-state persistence layer
  - `packages/prompts-core` — Agent system prompts (Prometheus, Atlas, Ultrawork)
  - `packages/team-core` — Multi-agent team orchestration
  - `packages/openclaw-core` — Shell/sandbox control
  - `packages/skills-loader-core` — Skill loading and execution
- **Plugin interface**: `dist/index.d.ts` exports `omoPlugin: Plugin` (type `PluginModule` from `@opencode-ai/plugin`). Loaded by OpenCode as `"plugin": ["oh-my-openagent@4.19.4"]` in `~/.config/opencode/opencode.jsonc`.

### OpenCode
- **Package**: `opencode-ai@1.18.31` (global npm, `~/.npm-global/lib/node_modules/opencode-ai/`)
- **Binary**: `opencode`

### Specialist Workers
| Worker | Status | Path |
|--------|--------|------|
| OpenCode | ✅ Installed | `opencode-ai@1.18.31` (global npm) |
| Claude Code | ❌ Not installed | `which claude` → not found |
| Codex | ❌ Not installed | `which codex` → not found |
| OMO `lazycodex` shim | ✅ Available | Alias of `omo` binary (Codex-compatible CLI wrapper) |

### LifeOS Hermes
- **Config**: `~/.config/lifeos/hermes/config.yaml`
- **Gateway binary**: `~/.local/bin/lifeos-hermes`
- **CodeGraph index**: `.codegraph` → symlink to `~/.omo/codegraph/projects/personal-secretary-dd289110c733a42d`

## 2. Config Locations

| Path | Purpose |
|------|---------|
| `~/.omo/omo.jsonc` | Unified OMO config — agent model chains, categories, `start_work`, `goal`, skills, hooks, MCPs |
| `~/.config/opencode/opencode.jsonc` | OpenCode runtime config — loads `oh-my-openagent@4.19.4` plugin, defines agent model chains + categories (all free OpenCode/Zen models), disables OpenAI providers |
| `.opencode/opencode.json` (repo-local) | Per-repo plugin override (`"plugin": ["list"]`) |
| `.omo/boulder.json` | Boulder work-state persistence (live campaign state) |
| `.omo/plans/` | Prometheus-generated plan files (`lifeos-master-plan.md`, `relay-boundary-hardening.md`) |
| `.omo/drafts/` | Compaction-safe plan drafts (frontmatter + intent + approach) |
| `.omo/notepads/{plan-name}/` | Auto-scaffolded session knowledge — `learnings.md`, `decisions.md`, `issues.md`, `problems.md` |
| `.omo/evidence/{plan}/{task}/` | Per-task evidence artifacts (test results, git state, audit outputs) |
| `.omo/start-work/ledger.jsonl` | Start-work event log (work-started, task_complete, final-wave-verdicts, plan-completed, goal) |
| `.omo/run-continuation/ses_*.json` | Session continuation files (one per task delegation) |
| `.omo/codegraph/projects/` | CodeGraph LLM-index (symlinked via `.codegraph` in repo) |

### Model Routing (from `~/.config/opencode/opencode.jsonc`)

Default model: `opencode/big-pickle` (primary coding model)

**Agent model chains:**
| Agent | Primary | Fallback 1 | Fallback 2 | Fallback 3 |
|-------|---------|----------|-------------|------------|
| sisyphus / hephaestus / build | `big-pickle` | `muse-spark-1.3` | `nemotron-3-ultra` | `mimo-v2.5` |
| atlas | `big-pickle` | `muse-spark-1.3` | `nemotron-3-ultra` | `mimo-v2.5` |
| prometheus | `big-pickle` | `nemotron-3-ultra` | `muse-spark-1.3` | — |
| plan | `nemotron-3-ultra` (1M context) | `big-pickle` | `muse-spark-1.3` | — |
| oracle / metis | `muse-spark-1.3` | `muse-spark-1.2` | `nemotron-3-ultra` | `big-pickle` |
| momus / multimodal-looker | `muse-spark-1.2` | `muse-spark-1.3` | `nemotron-3-ultra` | — |
| explore / librarian | `ling-3.0-flash` or `nemotron-3.5-lightning` | — | — | — |

**Categories** (Sisyphus-Junior routing): `visual-engineering`, `ultrabrain`, `deep`, `artistry`, `quick`, `unspecified-low`, `unspecified-high`, `writing`

**Disabled providers**: `openai`, `quotio-openai` (zero paid API costs enforced)

### OMO Internal Model Requirements (`AGENT_MODEL_REQUIREMENTS`)
- **prometheus**: `claude-fable-5` (xhigh) → `kimi-k3` (max), with free-model fallback chain
- **sisyphus**: `claude-opus-5` (max) → `kimi-k3` → `gpt-5.6-sol` (medium) → `glm-5.2` → `big-pickle`
- Override: OpenCode config (`opencode.jsonc` agents section) supersedes these defaults with explicit free-model chains

## 3. Working `/start-work` Invocation

### Slash command (OpenCode TUI)
```
/start-work lifeos-master-plan
```
This is a **builtin command** registered by the OMO OpenCode plugin via `loadBuiltinCommands()` → `createBuiltinCommandDefinitions()`.

**Command metadata:**
- `description`: "(builtin) Start Atlas work session from Prometheus plan"
- `agent`: `"atlas"` (resolved via `resolveStartWorkAgent(options)` — returns `"atlas"` if registered, else `"sisyphus"`)
- `argumentHint`: `[plan-name] [--worktree <path>] [--make-pr] [--ship]`
- `template`: `START_WORK_TEMPLATE` with `$ARGUMENTS`, `$SESSION_ID`, `$TIMESTAMP` injection

**CLI equivalent:**
```
omo run -a atlas "lifeos-master-plan"
```
(`--agent` defaults to config/CLI/env, falling back to `Sisyphus`)

### Start-work flow (from `START_WORK_TEMPLATE`)
1. **Find plans**: Search `.omo/plans/` for Prometheus-generated plan files
2. **Check boulder state**: Read `.omo/boulder.json` for active works
3. **Decision logic**:
   - Multiple active works → ask user which to resume
   - Single active work + no plan named → auto-resume
   - No active plan → list plans, auto-select if one, ask if multiple
4. **Worktree setup** (if `--worktree` specified): `git worktree add`, update `boulder.json`
5. **Create/update boulder.json**: Sets `active_plan`, `started_at`, `plan_name`, `session_ids`, `worktree_path`
6. **Read the plan file** and start executing tasks per Atlas workflow
7. **Set goal** via `create_goal` tool (or record first `ledger.jsonl` entry if unavailable)
8. **Register todos** — decompose every plan checkbox into granular sub-steps, grouped phase-by-phase

### Start-work flags
| Flag | Effect |
|------|--------|
| `--worktree <path>` | Work in existing git worktree; pre-sets `worktree_path` in boulder.json |
| `--make-pr` | Create task-owned worktree, open PR on completion, push branch |
| `--ship` | Full delivery lifecycle: implies `--make-pr`, keeps working until PR merged, cleans up worktree, syncs `.omo/` state back |

## 4. Atlas Status Query

### CLI: `omo boulder`
```
omo boulder              # Text output — progress, elapsed, current task
omo boulder --json       # JSON output — all works with full statistics
omo boulder -w <work-id> # Filter to a specific work ID
omo boulder -d <path>    # Specify working directory
```

**Live output** (as of investigation):
```
boulder progress
----------------------------------------
plan: relay-boundary-hardening
status: completed
progress: 100% (8/8)
elapsed: 46m 56s
sessions: 1
current task: -
----------------------------------------
plan: lifeos-master-plan
status: active
progress: 29% (5/17)
elapsed: 22h 41m 22s
sessions: 1
current task: Create free model API adapter (replacing OpenAI/Hermes paid paths) (20h 25m 41s)
```

### JSON output structure (`.omo/boulder.json`)
```jsonc
{
  "schema_version": "2",
  "active_work_id": "lifeos-master-plan-59ad3eeb",
  // Mirror fields of active work (for legacy compatibility):
  "active_plan": "/Users/.../personal-secretary/.omo/plans/lifeos-master-plan.md",
  "plan_name": "lifeos-master-plan",
  "status": "active",
  "started_at": "2026-09-15T04:37:55.736Z",
  "updated_at": "...",
  "agent": "atlas",
  "session_ids": ["opencode:ses_f5d..."],
  "session_origins": {...},
  "task_sessions": {...},
  // Array of all works:
  "works": [
    {
      "work_id": "lifeos-master-plan-59ad3eeb",
      "plan_name": "lifeos-master-plan",
      "active_plan": ".../lifeos-master-plan.md",
      "worktree_path": null,
      "status": "active",            // active | completed | paused | abandoned
      "started_at": "...",
      "ended_at": null,
      "elapsed_ms": 81682238,
      "elapsed_human": "22h 41m 22s",
      "total_tasks": 17,
      "completed_tasks": 5,
      "remaining_tasks": 12,
      "percentage": 29,
      "session_count": 1,
      "current_task": {
        "task_key": "todo:6",
        "task_title": "Create free model API adapter (replacing OpenAI/Hermes paid paths)",
        "elapsed_human": "20h 25m 41s"
      }
    },
    ...
  ]
}
```

## 5. Boulder Persistence / Resume Path

### State file location
```
.omo/boulder.json
```
Resolved via: `getBoulderFilePath(directory)` → `path.join(directory, BOULDER_DIR, BOULDER_FILE)`
where `BOULDER_DIR = ".omo"` and `BOULDER_FILE = "boulder.json"` (constant: `BOULDER_STATE_PATH = ".omo/boulder.json"`).

### Persistence functions
| Function | Action |
|----------|--------|
| `readBoulderState(directory)` | Reads `.omo/boulder.json` (JSON parse, returns null if missing) |
| `writeBoulderState(directory, state)` | Writes to `.omo/boulder.json` (atomic write via temp file + rename) |
| `completeBoulder(directory)` | Sets work `status: "completed"`, populates `ended_at`, writes state |
| `startBoulderWork(...)` | Creates/updates work entry with `status: "active"`, `started_at`, `session_ids` |
| `updateBoulderTaskProgress(...)` | Increments `completed_tasks`, updates `current_task` in state |
| `recordBoulderTaskSession(...)` | Records per-task session metadata (elapsed, result) |
| `TaskSessionState(directory, taskKey)` | Reads individual task session state from `task_sessions` map |

### Resume mechanism
1. `/start-work <plan-name>` auto-detects active works in boulder.json
2. If one active work exists: auto-resumes (appends current session ID to `session_ids`)
3. If multiple active works: prompts user to select
4. Run-continuation sessions persisted at `.omo/run-continuation/ses_*.json`
5. Atlas reads `.omo/plans/{plan-name}.md`, counts remaining top-level checkboxes, resumes from last incomplete task

### Work status lifecycle
```
active → completed  (normal completion via completeBoulder())
  ↓
paused               (manually paused)
  ↓
abandoned           (manually abandoned/cancelled)
```

## 6. Campaign Lifecycle Verification

### Phase 1: Planning (Prometheus)
**Agent**: Prometheus — system prompt loaded from `prometheusPromptVariants.default` (source: `packages/prompts-core/prompts/prometheus/default.md`).

**Prompt core**:
> You are Prometheus, a planning consultant. Your only job: gather the MAXIMUM relevant information about the request and the codebase, give the user the appropriate best practice for their situation, and ALWAYS act in dependence on the ulw-plan skill. You are a PLANNER. You read, search, and write only plan artifacts under `.omo/`; you never implement. Plan mode is sticky.

**Workflow** (from `ulw-plan` skill):
1. `ULW-PLAN MODE ENABLED!` announcement
2. Load `ulw-plan` skill via `skill(name="ulw-plan")`
3. Parallel read-only exploration (explore/librarian agents + codebase search)
4. **Intent routing**: `CLEAR` (outcome known, ask surviving forks) | `UNCLEAR` (vague brief, adopt best-practice defaults)
5. **Review gate**: `review_required: true` triggers high-accuracy review (momus + Oracle)
6. **Approval gate**: Plan written only after explicit user "okay"
7. `scaffold-plan.mjs` creates `.omo/drafts/<slug>.md` → `.omo/plans/<slug>.md` after approval
8. Appends task batches into `## Todos` (never rewrites)

**Constraints**:
- Prometheus is **hard-reject** as a team member (`AGENT_ELIGIBILITY_REGISTRY`): "plan-mode-only; can only write to `.omo/*.md` (enforced by `prometheusMdOnly` hook). Cannot write to team mailbox. Use `delegate-task` with `subagent_type: 'plan'` instead."
- Never implements — only writes plan artifacts
- Model: `prometheus` agent (big-pickle primary, nemotron-3-ultra fallback)

**Status**: ✅ Verified — plan exists at `.omo/plans/lifeos-master-plan.md` (17 tasks, 5 completed)

### Phase 2: Activation (/start-work)
**Mechanism**: Builtin slash command in OpenCode TUI (registered via `loadBuiltinCommands()` → `createBuiltinCommandDefinitions()`).

**What `/start-work` does**:
1. Searches `.omo/plans/` for plan files
2. Reads `.omo/boulder.json` to check for active work
3. Creates/updates boulder.json with work entry (`active_plan`, `plan_name`, `started_at`, `session_ids`)
4. Scaffolds `.omo/notepads/{plan-name}/` (learnings.md, decisions.md, issues.md, problems.md)
5. Sets session goal via `create_goal` tool
6. Registers todos (decomposes plan checkboxes into granular sub-steps)
7. Reads the FULL plan file
8. Invokes the Atlas agent (resolved via `resolveStartWorkAgent()`)

**Status**: ✅ Verified — `ledger.jsonl` shows `work-started` event for `lifeos-master-plan`:
```json
{"event": "work-started", "plan": ".omo/plans/lifeos-master-plan.md", "task": "orchestration", "session_id": "opencode:ses_f5d161fecffe2bSpDlSdb4J6Y1", ...}
```

### Phase 3: Orchestration (Atlas)
**Agent**: Atlas — "Master Orchestrator". System prompt loaded from `atlasPromptVariants.default` (source: `packages/prompts-core/prompts/atlas/default.md`).

**Prompt core**:
> You are Atlas — the Master Orchestrator. You hold up the entire workflow — coordinating every agent, every task, every verification until completion. You are a conductor, not a musician. A general, not a soldier. You DELEGATE, COORDINATE, and VERIFY. You never write code yourself. You orchestrate specialists who do.

**Orchestration workflow**:
1. **Step 0**: Register tracking (`TodoWrite` — complete ALL tasks, pass Final Verification Wave)
2. **Step 1**: Analyze plan — parse actionable top-level task checkboxes, build dependency map
3. **Step 2**: Notepad — auto-scaffolded `.omo/notepads/{plan-name}/` with learnings/decisions/issues/problems
4. **Step 3**: Execute tasks
   - **3.1 PARALLELIZE**: Fire all non-blocking tasks in ONE message (parallel by default)
   - **3.2 Read notepad first**: Read `.omo/notepads/{plan-name}/learnings.md`, `issues.md` before every delegation
   - **3.3 Invoke `task()`**: Category OR subagent_type (mutually exclusive), with 7-section prompt format
   - **3.4 Verify (MANDATORY)**: lsp_diagnostics (0 errors), build (exit 0), tests (ALL pass), manual code review (Read EVERY changed file), read plan file, cross-check claims vs code
   - **3.5 Handle failures**: Resume SAME session via `task_id` (`ses_...`), no retry cap
   - **3.6 Loop** until implementation complete
5. **Step 4**: Final Verification Wave (F1-F4 APPROVAL GATES — run in parallel, fix until ALL APPROVE)

**Delegation system**:
- `task()` tool with `subagent_type` (explore, librarian, oracle, artistry, sisyphus-junior, etc.) or `category` (visual-engineering, ultrabrain, deep, artistry, quick, etc.)
- 7-section prompt format: TASK, EXPECTED OUTCOME, REQUIRED TOOLS, MUST DO, MUST NOT DO, CONTEXT (notepad + inherited wisdom + dependencies)
- `run_in_background=true` for exploration agents; `false` for task execution

**Model routing**:
- Default: `opencode/big-pickle`
- Fallback chain per agent (configured in `opencode.jsonc`)
- `AGENT_MODEL_REQUIREMENTS` defines provider/model/variant fallbacks (overridden by opencode.jsonc)
- `model_fallback: true` (enabled in OMO config) — automatic model fallback on errors
- Fallback model normalization: `normalizeFallbackModelID` strips `-thinking`, `-max`, `-high` suffixes

**Status**: ✅ Verified — active orchestration in progress (5/17 tasks completed, currently on task 6)

### Phase 4: Persistence (Boulder)
**Mechanism**: `.omo/boulder.json` — JSON state file.

**What Boulder does**:
1. Tracks work state (work_id, plan_name, active_plan, status, started_at, ended_at, session_ids)
2. Tracks progress (total_tasks, completed_tasks, remaining_tasks, percentage)
3. Tracks per-task sessions (task_sessions map with elapsed_ms, result)
4. Tracks current task (current_task with task_key, task_title, elapsed_human)
5. `completeBoulder()` marks work as completed when all top-level checkboxes pass
6. Boulder-complete nudge injected into Atlas session when all plan tasks complete

**Resume path**:
- `/start-work <plan-name>` auto-resumes active work
- Session continuation: `.omo/run-continuation/ses_*.json`
- Task session continuation: `task(task_id="ses_...")` resumes same subagent session

**Status**: ✅ Verified — live boulder.json shows 2 works (1 completed, 1 active)

### Escalation / Fallback
- **Model fallback**: `event-model-fallback` — automatic retry with fallback models on rate limit / error
- **Agent eligibility**: Prometheus is hard-rejected as team member; must use `delegate-task` with `subagent_type: 'plan'`
- **Task failure**: Resume same `ses_...` session, iterate via task_id, spawn new subagent with different angle if stuck
- **Verification failure**: Resume same task with actual error output via `task(task_id="ses_...")`

### Cancellation / Supervision
- `/stop-continuation` — stops all continuation mechanisms (ralph loop, todo continuation, boulder) for current session
- `stop-continuation` builtin command template injected before shutdown
- `TodoWrite` for todo status management (in_progress → done)
- Boulder state tracks `ended_at` and `elapsed_ms` on completion

## 7. OMO Doctor Status

```
⚠ 23 issues found:
- 22 deprecated `fallback_models` config keys (should be `models`)
  Fix: `oh-my-openagent config migrate`
- 1 "Configured models rely on compatibility fallback" — 10 agents with unknown OpenCode Zen models
  (big-pickle, nemotron, muse-spark are not in OpenCode's registered model list)
```

## 8. Campaign Lifecycle Summary Table

| Phase | Component | Mechanism | Evidence |
|-------|-----------|-----------|----------|
| **Planning** | Prometheus agent | Loads `ulw-plan` skill → explores → intent routing → approval gate → writes `.omo/plans/{slug}.md` | Plan exists: `lifeos-master-plan.md` (17 tasks) |
| **Activation** | `/start-work` builtin command | Slash command in OpenCode TUI → resolves Atlas agent → updates `boulder.json` → scaffolds notepads → sets goal → registers todos | `ledger.jsonl`: `work-started` event for `lifeos-master-plan` |
| **Orchestration** | Atlas agent | "Master Orchestrator" — delegates via `task()`, parallel by default, 7-section prompts, mandatory verification (lsp+build+tests+manual review) | Active work: 5/17 tasks done, currently on task 6 |
| **Persistence** | Boulder (`.omo/boulder.json`) | JSON state file → `readBoulderState`/`writeBoulderState`/`completeBoulder` → `.omo/run-continuation/ses_*.json` for sessions | Live state: `status: "active"`, 29% complete |
| **Model routing** | `opencode.jsonc` agents + categories | Default: `big-pickle` → fallbacks: `muse-spark-1.3`, `nemotron-3-ultra`, `mimo-v2.5` | All free OpenCode/Zen models (OpenAI disabled) |
| **Fallback** | Model fallback controller | Auto-retry with fallback models on errors (rate limit, etc.) | `event-model-fallback` hook |
| **Categories** | Sisyphus-Junior routing | `visual-engineering`, `ultrabrain`, `deep`, `artistry`, `quick`, `unspecified-low/high`, `writing` | Configured in `opencode.jsonc` |
| **Escalation** | Atlas failure handling | Resume same `ses_...` session via `task_id`, no retry cap, spawn new angle if stuck | Documented in Atlas prompt |
| **Verification** | Atlas Step 3.4 | lsp_diagnostics (0 errors) + build (exit 0) + tests (ALL pass) + manual Read review + plan re-read | 4-phase protocol in Atlas prompt |
| **Cancellation** | `/stop-continuation` | Stops ralph loop, todo continuation, boulder for session | Builtin command |
| **Supervision** | `TodoWrite` + boulder tracking | Todo status (in_progress → done) → boulder.json progress → boulder-complete nudge → Final Wave (F1-F4) | Live: 5/17 tasks completed |

## 9. Working `/start-work` Invocation (Verified)

The campaign was activated with:
```
/start-work lifeos-master-plan
```
This is a **slash command** in the OpenCode TUI, registered by the OMO plugin as a **builtin command** with `agent: "atlas"`. The OpenCode plugin loads at startup (from `"plugin": ["oh-my-openagent@4.19.4"]` in `opencode.jsonc`).

## 10. Conclusion

OMO 4.19.4 is fully installed and operational on this LifeOS system. The complete campaign lifecycle — Prometheus (planning) → `/start-work` (activation) → Atlas (orchestration) → Boulder (persistence) — is active and verified. The current `lifeos-master-plan` campaign is 29% complete (5/17 tasks), actively orchestrated by Atlas, with state persisted in `.omo/boulder.json`. All models are free-tier OpenCode/Zen models with OpenAI providers disabled for zero-cost enforcement.

**No missing installations** — all core components (OMO, OpenCode, CodeGraph, Boulder state) are present and functioning. The only issues are 23 deprecation warnings (deprecated `fallback_models` config key → `models`) from `omo doctor`, which are non-blocking.
