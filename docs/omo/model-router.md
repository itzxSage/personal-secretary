# Model Router V1 — Free-by-Default, Paid Confined to Review

> Status: **Active** — Model Router V1 (2026-09-16). All production agent/category
> chains are pinned to zero-cost free models. The single paid model
> (`openai/gpt-6-astra`) is confined to the dev-harness reviewer role and is
> **not** reachable from any production service chain.

## 1. Tier Model

| Tier | Meaning | Cost | Where used |
|------|---------|------|------------|
| **free** | Default. Every agent/category chain is composed of zero-cost models. | $0 | All production chains in `~/.config/opencode/opencode.jsonc`, `~/.omo/omo.jsonc`, project `.opencode/opencode.json` |
| **affordable** | Optional. Low-cost paid models may be added per-task when a free chain is insufficient. | > $0 | Not currently configured. Reserved for future opt-in use. |
| **premium** | Gated. Reviewer-only tasks that require a paid frontier model. | > $0 | `tools/dev_harness/routing.json` → `senior_manual` → `openai/gpt-6-astra` (dev-harness-only, reviewer-only) |

**Rules:**
- **free = default**: every agent and category must resolve to a free chain. No
  paid model may appear in any production service chain.
- **affordable = optional**: adding a paid model is an explicit, documented
  decision; it must never be a silent fallback.
- **premium = gated reviewer tasks**: paid frontier models are reserved for the
  dev-harness `senior_manual` reviewer role and are hard-blocked everywhere else
  via `disabled_providers: ["openai", "quotio-openai"]` in
  `~/.config/opencode/opencode.jsonc`.

## 2. Source of Truth

`tools/dev_harness/routing.json` is the canonical role→model map:

| Role | Model | Tier |
|------|-------|------|
| `builder` | `opencode/nemotron-3-ultra-free` | free |
| `debugger` | `opencode/nemotron-3-ultra-free` | free |
| `verifier` | `opencode/mimo-v2.5-free` | free |
| `planner` | `opencode/mimo-v2.5-free` | free |
| `plan_reviewer` | `opencode/nemotron-3-ultra-free` | free |
| `integration_reviewer` | `opencode/mimo-v2.5-free` | free |
| `explorer` | `opencode/nemotron-3.5-lightning-free` | free |
| `senior_manual` | `openai/gpt-6-astra` | **premium (reviewer-only)** |

The three free models named in routing.json — `nemotron-3-ultra-free`,
`mimo-v2.5-free`, `nemotron-3.5-lightning-free` — are present in every
production chain as primary or fallback entries.

## 3. Free Model Inventory (verified $0)

All models referenced by the production configs, verified against the local
opencode model cache (`~/.cache/opencode/models.json`, sourced from
models.dev):

| Model | Input $ | Output $ | Context |
|-------|---------|----------|---------|
| `opencode/big-pickle` | 0 | 0 | 200 000 |
| `opencode/nemotron-3-ultra-free` | 0 | 0 | 1 000 000 |
| `opencode/mimo-v2.5-free` | 0 | 0 | 200 000 |
| `opencode/nemotron-3.5-lightning-free` | 0 | 0 | 262 144 |
| `opencode/muse-spark-1.3-contributor-free` | 0 | 0 | 1 048 576 |
| `opencode/muse-spark-1.2-contributor-free` | 0 | 0 | 1 048 576 |
| `opencode/ling-3.0-flash-fin-free` | 0 | 0 | 262 144 |

## 4. Config Surface

| File | Scope | Contents |
|------|-------|----------|
| `~/.config/opencode/opencode.jsonc` | user (global) | Session model, `disabled_providers` hard-block, agent/category free chains |
| `~/.omo/omo.jsonc` | user (OMO) | Agent/category free chains, `model_fallback`, `runtime_fallback` (429/5xx retry) |
| `.opencode/opencode.json` | project | Plugin list only — no model assignments |
| `tools/dev_harness/routing.json` | repo | Role→model map incl. the single paid `senior_manual` entry |

## 5. Deprecation Cleanup (fallback_models → models)

OMO 4.19.4 deprecates `fallback_models` in favor of `models`:

- **Categories**: migrated to `models` in `~/.omo/omo.jsonc` (8 entries) and
  `~/.config/opencode/opencode.jsonc` (8 entries). Valid — `CategoryConfigSchema`
  accepts `models`.
- **Agents**: **kept as `fallback_models`** — OMO 4.19.4's
  `AgentOverrideConfigSchema` does not list `models`, so renaming agent
  `fallback_models` → `models` makes the whole `agents` section fail validation
  and the fallback chains get stripped at parse time (behavior loss). The
  runtime (`resolveAgentDefinition`) already reads `models`, so this is a
  validation-schema bug in 4.19.4, not a config error. Revisit after an OMO
  release that adds `models` to `AgentOverrideConfigSchema`.

## 6. Paid Model Confinement

- `openai/gpt-6-astra` appears **exactly once** in the config surface: the
  `senior_manual` entry in `tools/dev_harness/routing.json`.
- `grep` for paid model ids over `~/.config/opencode/opencode.jsonc`,
  `.opencode/opencode.json`, and `~/.omo/omo.jsonc` returns **zero hits**.
- `disabled_providers: ["openai", "quotio-openai"]` hard-blocks every GPT
  provider so no agent chain can resolve to a paid GPT model.
- The OpenRouter credential lives in macOS Keychain
  (`LifeOS-Hermes-OpenRouter-Key`) and is never written to config or logs.

## 7. Known OMO 4.19.4 Limitations

1. **Agent `models` schema gap**: the deprecation check recommends
   `fallback_models` → `models` for agents, but `AgentOverrideConfigSchema`
   rejects `models`. Agent fallback chains must stay on `fallback_models` until
   the plugin schema catches up.
2. **Compatibility-fallback warning**: `omo doctor` flags `opencode/*` models as
   "unknown" because the bundled model-capabilities snapshot (generated
   2026-07-29) contains no `opencode` provider entries and no heuristic family
   matches them. This is informational; the models are valid and free.