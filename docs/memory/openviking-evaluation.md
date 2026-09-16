# OpenViking Evaluation — as a LifeOS Long-Term Memory Backend

**Source repo:** [volcengine/OpenViking](https://github.com/volcengine/OpenViking)
**Clone inspected:** HEAD at `main` (2026-09-15)
**Upstream version referenced:** 0.4.9 (changelog top), changelog "Unreleased" section active
**License:** AGPL-3.0
**Python requirement:** 3.10+
**Primary docs:** https://docs.openviking.ai/en/

> **Verification status:** This evaluation is based on the live upstream repository
> (cloned fresh into `/tmp/OpenViking`) plus the official published docs and the
> bundled Hermes OpenViking MemoryProvider source (3 725-line single file at
> `hermes-agent/.../plugins/memory/openviking/__init__.py`, baseline
> `e12626b3` ≈ brew 2026.7.7.2). All claims below are traceable to those sources.

## 0. Executive TL;DR (LifeMemory mapping)

| LifeMemory abstraction method | OpenViking reality | Verdict |
|---|---|---|
| `remember(content)` | `viking_remember` tool (Hermes) → creates a one-shot session → Phase-1 archive + async Phase-2 LLM memory extraction & dedup; also `POST /api/v1/sessions/{id}/messages` + `commit`. **Success = "submitted", not "memory created"** — OpenViking may skip/merge. | ✅ Covered; semantics-aware |
| `recall(query)` | `POST /api/v1/search/search(mode="context")` (budgeting + tier degradation + cross-turn dedup) or `/api/v1/search/find` (vector-only). `viking_search` tool wraps both. | ✅ Covered |
| `search(query)` | Same as recall above; `find` = fast vector, `search` = intent-expanded. | ✅ Covered |
| `retrieve_context(query, session_id, token_budget)` | `search(mode="context")` with `max_tokens`, `purpose`, `detail` tiers, `dedup_turns`, server-side `rewrite` digest. | ✅ Covered; **native** equivalent |
| `correct(uri, content)` | No single primitive. Path A: write to the file via `viking_add_resource`/`write` API (direct, bypasses extraction). Path B: `viking_remember` a corrected statement and let the LLM merge. | ⚠️ Must be synthesized at the abstraction layer |
| `invalidate(uri)` | No soft-invalidate. `viking_forget` is a **permanent** delete (Hermes guards it strictly: only `…/memories/…/mem_*.md` leaf files, never summaries/dirs). | ⚠️ No STALE state; only hard delete |
| `mark_stale(uri)` | **Not present.** Hotness scoring (`active_count` + `updated_at` decay) ranks stale items lower in retrieval but never marks them. | ❌ Gap — must emulate via retrieval score or custom metadata |
| `resolve_conflict(uri_a, uri_b)` | **Not present** as an API. The session commit extraction loop performs LLM dedup with skip/merge/delete *decisions* and writes a `memory_diff.json` audit, but there is no runtime-conflict state machine and no `CONFLICTED` marker a LifeMemory layer can read. | ❌ Gap |
| `provenance(uri)` | `MemoryOperationSource` embeds `extraction_id`, `session_id`, `archive_uri`, `task_id`, `trace_id`, `extracted_at`. `find`/`search` accept `include_provenance=true` to return query-plan details. `memory_diff.json` records per-commit adds/updates/deletes with `before/after`. **Per-fact provenance (how this specific claim was derived) is NOT captured.** | ✅ Partial |
| `forget(uri)` | `viking_forget` (permanent delete, type-bounded). `DELETE /api/v1/fs?uri=…&recursive=False`. | ✅ Covered (hard-only) |

---

## 1. Upstream feature verification

All items below are **confirmed present** in the current upstream source/docs unless marked otherwise.

### Resources · Memories · Skills (3 context types)

`docs/en/concepts/02-context-types.md` defines three types, each addressable under `viking://`:

- **Resource** — external/objective knowledge; user-added; static; long-term.
  - `viking://resources/{project}/…` (account-shared) or `viking://~/resources/…` (user-private).
- **Memory** — cognition learned from interactions; agent-driven; dynamic; lives under `viking://~/memories/`.
- **Skill** — declarable AgentDefinedContextType; `viking://~/skills/{name}/` (user) or `viking://agent/skills/{name}/` (global shared).

Built-in memory types (`docs/en/concepts/02-context-types.md` §Memory):

| Type | Default location | Purpose |
|---|---|---|
| profile | `memories/profile.md` | Basic user info |
| preferences | `memories/preferences/` | User preferences by topic |
| entities | `memories/entities/` | People, projects, organizations |
| events | `memories/events/` | Decisions, milestones |
| identity | `memories/identity.md` | Assistant name/persona |
| soul | `memories/soul.md` | Principles, boundaries, style |
| cases | `memories/cases/` | Trainable/evaluatable task cases |
| trajectories | `memories/trajectories/` | Reusable execution contracts |
| experiences | `memories/experiences/` | Insights distilled from outcomes |
| tools | `memories/tools/` | Tool usage knowledge |
| skills | `memories/skills/` | Skill execution knowledge |

Custom memory templates are supported (override/extend); see `memory_policy.memory_types`.

### Viking URI system

`docs/en/concepts/04-viking-uri.md`:

- **Format:** `viking://{scope}/{path}`.
- **Public scopes:** `resources` (account-global, ACL-aware), `user/{user_id}` (current user), `agent` (account-global agent config).
- **Internal scopes:** `temp`, `queue`, `upload` (cannot be addressed via public API URI params).
- **Home alias `~`** expands server-side to `viking://user/{user_id}` for the authenticated caller; rejected for dev/ROOT without a USER/ADMIN role.
- **File IDs:** stable `md5("{account_id}:{uri}")` for L2 records; directories span multiple records (one per semantic level).
- Canonical URIs are always echoed in responses; persisted vector records stay canonical (never `viking://~`).
- `session` is a backward-compatible alias; new data lives under `viking://user/{user_id}/sessions/{session_id}`.

### Directory-aware retrieval

`docs/en/concepts/07-retrieval.md` + source `openviking/retrieve/hierarchical_retriever.py`:

- Two-stage: **intent analysis** → **hierarchical retrieval** → **rerank**.
- `find()` = vector-only, low latency, no session context.
- `search()` = LLM intent analysis generates 0–5 `TypedQuery` (skill/resource/memory), session-aware query expansion, rerank.
- **HierarchicalRetriever** uses a priority queue to recurse the directory tree: global vector search locates starting directories, then recursively explores children, propagating a blended score (`score_propagation_alpha` configurable, default 1.0).
- Convergence detection stops recursion after 3 rounds of no new top-k improvement (`MAX_CONVERGENCE_ROUNDS`).
- `target_uri` scopes the search to a path prefix; `context_type` filters by memory/resource/skill.
- Rerank: scalar (level, time) filtering + model rerank; falls back to vector scores on API failure.

### Layered summaries (L0 / L1 / L2)

`docs/en/concepts/03-context-layers.md`:

- **L0 (Abstract)** — `.abstract.md`, ~256 chars, vector retrieval & quick relevance.
- **L1 (Overview)** — `.overview.md`, ~4 000 chars, rerank & navigation.
- **L2 (Detail)** — original files, loaded on demand.
- L0/L1 are **directory-level** sidecars (not per-file); file summaries aggregate into the parent directory's L1.
- L0 is extracted from L1 body (the Brief Description paragraph after H1).
- **OKF Markdown** format: YAML frontmatter (`directory`, `source`, `generated_by`, `freshness`) + body. Unknown top-level fields are silently dropped.
- **Freshness** tracks direct-child coverage (`total_entries`, `sampled_entries`, `unsampled_entries`, `pending_child_changes`); deterministic stable sampling when children exceed `semantic.overview_sample_limit` (default 32).
- **Tiered loading** is the headline efficiency trick: every entry is processed into L0/L1/L2 on write, then loaded only as deep as the task requires.

### Session-to-memory extraction

`docs/en/concepts/08-session.md` + source `openviking/session/memory/extract_loop.py`, `openviking/session/memory/memory_policy.py`:

- **Lifecycle:** create → interact (messages) → commit.
- **Two-phase commit:** Phase 1 (sync) writes `messages.jsonl` + increments `compression_index` + clears live messages; Phase 2 (async background) generates `.abstract.md`/`.overview.md` + extracts long-term memories + writes `memory_diff.json` + updates `active_count` + writes `.done` marker.
- **Extraction flow:** `Messages → LLM Extract → Candidate Memories → Vector Pre-filter → Find Similar → LLM Dedup Decision → Write to AGFS + Vectorize`.
- **Dedup decisions:** per-candidate `skip` / `create` (optionally deleting conflicting existing) / `none`; per-existing `merge` / `delete`.
- **Memory policy** (`memory_policy.py`, source-validated): `self.enabled`, `peer.enabled`, `memory_types` (set), `working_memory.enabled`. Precedence: session policy > user `settings/user_config.json` > `server.user_config_defaults.memory_policy` > kernel default.
- `experiences` automatically activates `cases` + `trajectories`; without it they are ignored (no error).
- `memory_diff.json` audit log: `operations.adds/updates/deletes` (with `before/after`), `skipped_operations` (stable `MemoryOperationSkipCode`), `summary` counts. Written per commit for auditing/rollback.

### Consolidation (memory dedup / merge)

`docs/en/concepts/08-session.md` §Dedup Decisions + source `openviking/session/memory/merge_op/*`, `openviking/session/memory/merge_policy.py`:

- LLM-driven dedup with a **shared merge policy** (`merge_policy.py`) that forbids merging on similarity alone — same identity under the active memory schema is required.
- Merge strategies by field type (`merge_op/`): `Patch`, `Replace`, `Sum`, `Immutable`, `LinkMerge` (with conflict resolution: "weight conflict: take max").
- Cross-template file conflicts rejected before any batch write (`ConflictError`); same-batch upsert/delete URI conflicts rejected.
- `MemoryOperationSource` carries `extraction_id` so streaming-batch patch merges can detect batches from different extraction snapshots.

### SDK / API

- **Python SDK:** `openviking_sdk` (`SyncHTTPClient`/`AsyncHTTPClient`), imports `viking://`, `viking_remember`-equivalent session API, `find`/`search`/`search_context`/`grep`/`glob`/`read`/`write`/`ls`/`tree`/`stat`/`abstract`/`overview`/`set_tags`/`acl_*`/`add_resource`/`add_skill`/`session(...).add_message/commit`/`get_session_context`/`get_archive`/observer.
- **Go SDK:** `sdk/go` module, HTTP-only, same identity headers.
- **TypeScript SDK:** `@openviking/sdk`, ESM+CommonJS+types.
- **HTTP API:** `POST /api/v1/search/find`, `/api/v1/search/search(mode="context")`, `/api/v1/sessions/{id}/messages[/{id}/commit]`, `/api/v1/content/{read|write|abstract|overview}`, `/api/v1/fs/{ls|tree|stat|rm|mv}`, `/api/v1/resources`, `/api/v1/skills`.
- **Native MCP:** built into `openviking-server` (`/mcp`), same auth, all 15 tools.
- **CLI:** `ov` (Rust binary), `ov find/search/read/write/ls/tree/forget/add-memory/add-resource/add-skill/session commit/...`.

### Hermes integration

`docs/en/agent-integrations/05-hermes.md` + `docs/en/agent-integrations/16-capability-reference.md` §hermes + the bundled provider source:

- **Form:** Native in-process `MemoryProvider` plugin, shipped **with** Hermes (not installed separately). Connects directly via `httpx` — no SDK/CLI dependency in the Hermes environment.
- **Tools (6):** `viking_search`, `viking_read`, `viking_browse`, `viking_remember`, `viking_forget`, `viking_add_resource`.
- **Hooks:** `prefetch` (session-start recall + query recall), `sync_turn` (record completed turn), `on_session_end`, `on_session_switch`, `on_memory_write` (mirror built-in memory writes to OV), plus `atexit`/signal finalizers.
- **Config:** `OPENVIKING_ENDPOINT`/`_API_KEY`/`_ACCOUNT`/`_USER`/`_AGENT` env vars, or `config.yaml` (`memory.openviking.*`), or `use_ovcli_config` linking `~/.openviking/ovcli.conf`. SSRF floor check + local-server autostart (subprocess) + identity probe (`modern`/`legacy`/`invalid`).
- **Recall params (Hermes-side):** 6 results, 0.15 threshold, 4 000-char injection budget, 4 s total / 3 s per-request timeout, session IDs `%Y%m%d_%H%M%S_<hex6>`.
- **URI scheme for Hermes memories:** `viking://~/peers/{agent}/memories/{subdir}/mem_{uuid12>.md` (peer = `OPENVIKING_AGENT`, default `hermes`).
- **Commits:** always `keep_recent_count=0`; triggers on `on_session_end`, `on_session_switch`, gateway cache eviction, `atexit`; 1.5 s SIGTERM grace + 30 s exit watchdog. Idempotent per-session; in-process (non-disk) queue.
- **Write path:** structured batch (≤100 msgs) → plain-text fallback → individual-message fallback; recall-tool calls/results dropped so recalled memory isn't re-ingested.
- **Notable gaps vs. other harnesses (per §1.2/§1.3):** No profile injection at session start, no status line, no slash commands, no compaction takeover. Recall on the preferred path lands in server Path B (`mode="deep"`) — session_id is **partial**: carried on `search/search` but **dropped** when degrading to `/find`.

---

## 2. LifeOS memory-requirement evaluation

### 2.1 Retrieval quality

**Score: Strong.**

- LoCoMo benchmark: **80–83 % accuracy** with OpenViking vs **24–57 %** native (per README benchmark blurb). All three agent integrations (Claude Code, OpenClaw, Hermes) land in the 80–83 % band.
- tau2-bench: task success **+6.87 pp (retail)** / **+11.87 pp (airline)** over the same LLM without memory.
- Hierarchical intent-aware retrieval (intent analysis → directory recursion → rerank) plus cross-turn dedup (`dedup_turns` ledger at `{session_uri}/.recall_log.json`) and optional server-side digest rewrite (`L3 rewrite`, `rewrite=auto` when a query-planner model is configured).
- **Caveat:** The third-party review (andrew.ooo) notes knowledge-base QA quietly shows OpenViking **losing to LightRAG on accuracy**. The memory benchmarks are strong; pure-RAG QA is a relative weakness.
- Hermes recall uses server Path B (`mode="deep"`) on the fast path — gets intent expansion + rerank but **not** cross-turn dedup or the session-ledgb. Degradation to `/find` drops `session_id` entirely.

### 2.2 Context efficiency

**Score: Strong — this is OpenViking's flagship strength.**

- Token reduction **34.3–91.0 %** and latency reduction **58.45–66.10 %** (LoCoMo).
- The L0/L1/L2 tiered loading model is purpose-built for this: `search(mode="context")` budgets server-side into a single `max_tokens` (default 1 600) and degrades tier-by-tier (full → overview → abstract → uri) rather than truncating. Per-entry cap = `max_tokens / candidate_count × 2`.
- Default tier per category means most memory categories cost a read of **zero** content at the abstract level; only `events` reads a file by default.
- **Caveat for Hermes:** Hermes sets an explicit 4 000-char injection budget and does its own local candidate ranking/dedup on the `/find` fallback, which can overshoot relative to the server's token budgeting when it hits the degraded path.

### 2.3 Provenance

**Score: Good — partial per-fact attribution.**

- `MemoryOperationSource` (dataclass.py:157) persists per-extraction-run provenance: `extraction_id`, `session_id`, `archive_uri`, `task_id`, `trace_id`, `extracted_at`.
- `find`/`search` accept `include_provenance=true` → returns query-plan details.
- `memory_diff.json` (written per commit) is a complete per-commit audit: `operations.adds/updates/deletes` with `before/after` content, plus `skipped_operations` with stable `MemoryOperationSkipCode` values.
- `MemoryOperationSkipCode` enum (dataclass.py:173): `MEMORY_TYPE_FILTERED`, `SELF_MEMORY_DISABLED`, `PEER_MEMORY_DISABLED`, `INVALID_PEER_ID`, `PEER_NOT_ALLOWED`, `INVALID_RANGES`, `AMBIGUOUS_TARGET`, `NO_WRITABLE_TARGET`, `PAGE_ID_TYPE_MISMATCH`.
- Vector collection schema carries `created_at`, `updated_at`, `active_count`, `account_id`, `owner_user_id`, `context_type` (collection_schemas.py).
- **Gap:** There is no per-fact *confidence* or *source-document* link embedded inside a memory file's body/structured fields. Provenance is run-level (which commit/extraction produced this) and query-level, not claim-level. A LifeMemory `provenance(uri)` would return extraction-run metadata, not "this fact came from message #n of session X."

### 2.4 Confidence states (CONFIRMED / OBSERVED / INFERRED / STALE / CONFLICTED / UNKNOWN)

**Score: ❌ Not present as first-class states.**

- No enum or persisted field carrying any of the six LifeOS confidence states. Memories are opaque `.md` files (or `.abstract.md`/`.overview.md` for directories).
- The only `confidence` in the codebase is `_PATCH_METADATA_KEYS = ("confidence", ...)` in `patch_merge_context_provider.py` — a **model-facing merge hint** for patch-merge decisions, not a persisted per-memory state visible to consumers.
- **Hotness scoring** (`retrieve/memory_lifecycle.py`) computes a 0–1 score from `sigmoid(log1p(active_count)) * exponential_time_decay(updated_at, half_life=7d)`. This is *recency+frequency* — it can drive a "likely stale → lower rank" policy but is not a STALE marker, and it's not exposed as a memory attribute.
- `active_count` (retrieval count) and `updated_at` are stored in the vector index scalar index and can be read back, so a LifeMemory adapter could *compute* a staleness proxy — but OpenViking itself has no `mark_stale`/`invalidate` and no CONFLICTED state.
- **No CONFLICTED state:** conflicts are resolved at extraction time (skip/merge/delete by LLM judgment) and audited in `memory_diff.json`, but once written, a memory is not marked "conflicted." Duplicate handling is "merge into one surviving file."

### 2.5 Temporality

**Score: ✅ Strong.**

- Vector schema records `created_at` and `updated_at` (both `date_time`, scalar-indexed).
- Retrieval supports `since`/`until` time bounds accepting `2h`/`7d` or ISO 8601 (`time_field` = `updated_at` default, also `created_at`).
- Session archive structure preserves history: `sessions/{id}/history/archive_NNN/{messages.jsonl, .abstract.md, .overview.md, memory_diff.json, .done}`.
- Hotness decay uses `updated_at` for recency.
- No per-memory *expiration TTL*, but the `working_memory.enabled=false` switch skips archive summaries, and `memory_diff.json` gives commit-level temporal granularity.

### 2.6 Sensitivity

**Score: ⚠️ Partial — no per-memory sensitivity labels.**

- **Encryption at rest** (`docs/en/concepts/10-encryption.md`): transparent envelope encryption — Root Key (KMS / Vault / local `~/.openviking/master.key`) → per-account KEK (HKDF) → per-file DEK (AES-256-GCM, new per write). Completely transparent; backward compatible with unencrypted files. Multi-tenant data isolation via account keys.
- **ACL** (`docs/en/concepts/15-acl.md`): resource-level sharing with `read`/`write`/`manage` levels, typed principals (`user:{id}`, `group:{id}`, `user:*`), inheritance + `restricted` boundary. **ACL applies only to `viking://resources/…`** — memories under `viking://user/…` are tenant/user-isolated and do not accept ACLs.
- **Privacy configs** (`docs/en/concepts/13-privacy.md`): skill-secret extraction/placeholderization with versioned snapshots + key rotation + read-time restore. Stored under `viking://user/{space}/privacy/{category}/{target_key}/`. **Skills only** — not memories.
- **No sensitivity classification on memories.** There is no `sensitivity: secret|personal|pii` field, no per-memory redaction policy, no selective non-indexing. A memory containing PII is encrypted + account-isolated + not ACL-shareable, but cannot be tagged/redacted/held-back selectively.
- Hermes provider mirrors built-in memory writes via `on_memory_write` into the agent peer's `memories/preferences` or `memories/patterns` — same sensitivity posture (no labels).

### 2.7 Conflict handling

**Score: ⚠️ Weak — extraction-time dedup only, no runtime CONFLICTED state.**

- Conflicts are resolved **at extraction**, not surfaced to consumers. The `ExtractLoop` (ReAct, single LLM call with tool use) produces candidate memories; a vector pre-filter finds similar existing ones; the LLM decides **skip** (duplicate) / **create** (optionally delete conflicting) / **none**, and **merge** / **delete** per existing item.
- `merge_policy.py` is a shared model-facing guardrail: similarity/overlap is **not** sufficient to merge; same identity under the active memory schema is required; facts must be preserved exactly once.
- Cross-template file conflicts are rejected before writes; same-batch upsert/delete URI conflicts rejected.
- `memory_diff.json` is the audit artifact (adds/updates/deletes with before/after + skip reason codes).
- **No runtime `resolve_conflict`/`mark_conflicted` API.** Once a memory is written, there is no CONFLICTED marker and no reconciliation endpoint. A LifeMemory `resolve_conflict` would have to be expressed as: rewrite one side (via `write`) + delete the other (via `forget`), then re-extract — not a native capability.

### 2.8 Portability

**Score: ✅ Strong.**

- **OVPack** (`docs/en/guides/09-ovpack.md`): `ov export`/`import`/`backup`/`restore` with v2 manifests, file checksums, optional dense-vector snapshots, and conflict policies (`fail`/`overwrite`/`skip`). Import/export target any `viking://` root.
- **Snapshots** (`ov snapshot`): workspace snapshots with rollback-by-forward.
- **AGFS backends:** `localfs`, `s3fs` (S3-compatible), `memory` (tests). Multi-write mode (`storage.agfs.backups`) enables replicas + migration.
- **SDKs** in Python / Go / TypeScript connect over HTTP — no local state coupling. The HTTP API is the portable surface.
- **Hermes:** ships no local state outside `HERMES_HOME/openviking/pending_sessions/*` (non-disk queue); `backup_paths()` returns the linked `ovcli.conf`. So portability is entirely server-side (AGFS + vector index) — good, but means a LifeMemory abstraction must treat OV as a remote service, not a local store.

### 2.9 Privacy

**Score: ✅ Good (encryption + ACL + tenant isolation), ⚠️ with caveats.**

- **At-rest encryption** (envelope, AES-256-GCM per file) with local/Vault/KMS key providers — tenant-isolated keys.
- **Tenant isolation:** `account` (outer) + `user` (inner) boundaries; ROOT/ADMIN/USER roles; dev mode only on localhost.
- **ACL** for shared resources; `restricted` mode stops inheritance; `group:*` unsupported, groups are flat.
- **Peer isolation:** `X-OpenViking-Actor-Peer` filters `viking://user/{user}/peers` to one peer for both filesystem and retrieval; peer must be a safe single path segment.
- **Hermes** writes to `viking://~/peers/{agent}/memories/…` — the `agent` config acts as the peer boundary so the assistant has its own memory space distinct from self/user memories.
- **Caveats:** No encryption *in transit* beyond TLS (assumed by HTTPS); no per-field/PII redaction; ACL cannot be applied to memories; privacy configs are skill-only.

### 2.10 Migration

**Score: ✅ Good — multiple, versioned paths.**

- **OVPack import/export** (v2 manifests, checksums, conflict policies) — the primary portability/migration vehicle.
- **Snapshot restore** (`ov restore`) covers full public-scope migration; `--on-conflict overwrite/skip`.
- **0.3.x → 0.4.0 upgrade guide** with a live compatibility matrix and an `ov --sudo admin migrate` task (legacy `agent_id`/`viking://agent/`/`viking://session/` readable but not writable; migrated to `viking://user/{user}/peers/{peer}/…`).
- **Log ingestion** (`openviking-server ingest`, adapters for claude_code, codex, hermes, opencode, openclaw, cursor): offline replay of legacy transcripts into OV sessions with crash recovery (SQLite cursor store + reconciliation) — explicitly disabled by default.
- **Multi-write storage** enables live migration between backends (`.sync_log.json` + `.redirect.json` internals).
- **External peer identity migration:** mixed-script log-ingestion identities move to lossless `ext-<base64>` ids (lossy older dirs not auto-read — operator decision required).

### 2.11 Authority boundaries

**Score: ✅ Strong.**

- Three identity layers: `account` (workspace/team), `user` (per-account person), `actor_peer` (a content scope *inside* user, e.g. `web-visitor-alice` or the Hermes `agent` id). Peer is **not** a tenant — it never crosses account/user.
- Role model: ROOT (global), ADMIN (single account), USER (own data + shared resources).
- **User-space resolution is server-asserted** when `viking://~` is used; the Hermes provider even probes `/api/v1/system/status` to resolve the explicit user uid (rather than guessing), and rejects uid-less `viking://user/memories` spells.
- **Hermes peer = `OPENVIKING_AGENT`** — assistant memories are isolated under `peers/{agent}/memories`, cleanly separating assistant-owned facts from user facts (`preferences`/`patterns`).
- Write/delete boundaries are hardened (§3.5): `skills/`, `peers/`, `privacy/`, `sessions/` under user root are **read-only** for public write APIs; `viking_forget` on Hermes is memory-only + `.md` + leaf check, so an assistant cannot delete user preferences, resources, or skills. This is a strong authority guard.

---

## 3. Hermes integration specifics (source-verified)

The bundled provider (`plugins/memory/openviking/__init__.py`) is a **native in-process `MemoryProvider`**, not a plugin download. Key facts from the source:

- Connects via a minimal `httpx` client (`_VikingClient`) — no `openviking_sdk` dependency in the Hermes venv (intentionally kept separate per the integration docs: "do not `--force-reinstall` OpenViking into the Hermes environment").
- `is_available()` checks config only (env `OPENVIKING_ENDPOINT`, `config.yaml` `memory.openviking`, or linked `ovcli.conf` with `use_ovcli_config`). No network.
- `initialize()` resolves env → ovcli → config.yaml → default (`http://127.0.0.1:1933`), probes identity (`_probe_openviking_identity` → `modern`/`legacy`/`invalid`), and **auto-starts a local `openviking-server` via `subprocess.Popen`** if the loopback endpoint is unreachable and unoccupied (with a 60 s health wait). SSRF floor (`is_always_blocked_url`) rejects metadata addresses.
- **6 tools** with typed schemas; names normalized to `viking_*`. `viking_remember` spins up a dedicated session `hermes-remember-{uuid12}`, POSTs one user message, then commits **synchronously** (Phase 1) — the Phase-2 extraction is async/background on the server, so the tool returns `status: submitted` + a `task_id`/`recovery_command`.
- `viking_forget` validation (source `_validate_forget_memory_uri`): rejects non-`viking://` schemes, query/fragment params, directories, non-`.md` files, the `memories/`/category roots themselves (needs ≥2 segments after `memories`), and `.abstract.md`/`.overview.md` — so the assistant cannot nuke a category or a generated summary.
- `on_memory_write` mirrors built-in `memory(action=add)` writes to `viking://~/peers/{agent}/memories/{preferences|patterns}/mem_{uuid12}.md` via the content-write API (file-direct, no extraction).
- `sync_turn` converts Hermes canonical messages to OV batch payloads (`POST /api/v1/sessions/{sid}/messages/batch`, ≤100); recall tools are stripped from the batch; multi-tier fallback (structured → plain-text → individual messages).
- **Shutdown:** `atexit` + `_signal_handler` (1.5 s SIGTERM/SIGHUP grace) commit the live session; 30 s exit watchdog kills slow commits. Fork-style compaction (in-place compression triggers nothing; `/new`/`/reset`/`/branch`/compression forks commit the old session asynchronously).

---

## 4. LifeMemory abstraction compatibility matrix

| LifeMemory op | Maps to OV | Notes / friction |
|---|---|---|
| `remember(content)` | `viking_remember` / session commit | Async extraction; caller must poll `task_id`; semantics are "submitted → OV decides skip/merge/create" |
| `recall(query)` | `search(mode="context")` or `find` | Native token budgeting; Hermes falls back to `/find` without session_id on the fast path |
| `search(query)` | `find` / `search` | Direct |
| `retrieve_context(query, budget)` | `search(mode="context", max_tokens=…)` | **Near-perfect** native match |
| `correct(uri, new_text)` | `write` (file-direct) | No "correct with provenance" — direct overwrite or re-remember; must synthesize |
| `invalidate(uri)` | `viking_forget` | **Permanent** delete only; no STALE/soft-invalidate |
| `mark_stale(uri)` | **No equivalent** | Must emulate via `hotness_score` or a custom metadata field (write a tag + filter); not native |
| `resolve_conflict(a, b)` | **No equivalent** | Extraction-time dedup only; runtime conflict state absent |
| `provenance(uri)` | `include_provenance` + `memory_diff.json` + `MemoryOperationSource` | Run-level provenance only; no per-claim attribution |
| `forget(uri)` | `viking_forget` | Hard delete; type-bounded on Hermes |

---

## 5. Assessment

### Strengths (why OpenViking is attractive as a backend)

1. **Purpose-built agent memory** with proven accuracy gains (80–83 % LoCoMo) and large token/latency reductions — directly addresses LifeOS goals for retrieval quality and context efficiency.
2. **Tiered L0/L1/L2 loading** is a first-class system primitive, not an afterthought — `search(mode="context")` budgets and degrades tiers server-side, which is essentially a LifeMemory `retrieve_context` with built-in efficiency.
3. **Strong builtin Hermes integration** — it is the *only* harness in OpenViking's integration matrix described as "native registration" with a single-file in-process provider; no MCP proxy, no env-file drift from the Hermes venv, auto-start of a local server, SSRF guards, and atexit/signal commit.
4. **Provenance & audit trail** at the commit level (`memory_diff.json` with `before/after`, `MemoryOperationSource` carrying `extraction_id`/`session_id`/`trace_id`, `include_provenance` query plans) gives a real foundation for a `provenance()` op.
5. **Robust consolidation** — LLM dedup with a conservative merge policy and skip/merge/delete decisions, plus an auditable diff — is exactly the kind of session→memory extraction LifeMemory needs.
6. **Authority boundaries are hardened** — Hermes's `viking_forget` cannot delete a memory category, a directory, a generated summary, or anything outside `…/memories/…/*.md`; the assistant's peer isolation (`peers/{agent}`) separates assistant facts from user facts.
7. **Portability & migration** — OVPack (v2), snapshots, multi-backend AGFS, 0.3→0.4 migration, and hermes-adapter log ingestion all provide real migration story.

### Gaps (where OpenViking does NOT give LifeOS a first-class primitive)

1. **No confidence-state model.** CONFIRMED/OBSERVED/INFERRED/STALE/CONFLICTED/UNKNOWN are absent. The only `confidence` is a transient model-facing patch-merge hint. Hotness scoring (recency+frequency decay) can *inform* a staleness policy but is not a STALE marker, and nothing marks CONFLICTED.
2. **No soft-invalidate / mark_stale / invalidate.** Only `viking_forget` (permanent, unrecoverable delete). There is no tombstone, no retention policy API, no "hold but don't surface" state.
3. **No runtime conflict resolution.** Conflicts are resolved once at extraction time; there is no `resolve_conflict` endpoint and no CONFLICTED state a consumer can read and act on. The MemoryProvider `correct`/`invalidate` methods don't exist.
4. **Provenance is run-level, not claim-level.** You can trace a memory file to its extraction run/session/trace, but not to the specific message or tool result that produced an individual fact inside it.
5. **No per-memory sensitivity labels.** PII/secret redaction, selective non-indexing, and per-memory ACLs are not available for memories (ACL is resources-only; privacy configs are skill-only). Reliance is on account-level encryption + tenant/user/peer isolation.
6. **Hermes recall uses the degraded retrieval path** on the fast path: `search(mode="context")` carries session_id and gets Path A (query expansion + ledger dedup), but `prefetch`'s preferred path routes to `mode="deep"` (Path B, no ledger) and the `auto`/`fast` fallback calls `/find` with **no session_id at all**. This is an integration-layer detail, not an OV core defect, but it caps recall quality for the built-in provider.
7. **License: AGPL-3.0.** Self-hosting is fine for internal/private use, but any modification + distribution of a modified server carries source-disclosure obligations. Not a problem for a pure client/abstraction layer, but material if LifeOS forks the server.

---

## 6. Recommendation

**C — put OpenViking behind a LifeMemory abstraction layer**, and implement the confidence-state / invalidate / resolve-conflict / correct semantics in Hermes-land (or in the LifeMemory shim), persisting confidence as Hermes-side metadata (e.g., a `mem_<uuid12>.ovmeta` sidecar or a Hermes-local SQLite map of `uri → {confidence, valid, source}`) that the abstraction consults at `recall`/`search` time as a post-filter. Rationale:

- OpenViking is **too strong to discard** (A would be over-commit given the gaps) but **too opinionated to adopt raw** (it has no confidence states, no soft-invalidation, no conflict state, no claim-level provenance).
- A Hermes-only backend (B) loses OV's superior extraction quality, tiered retrieval, and provenance/audit trail — and the Hermes OpenViking provider already exists and is the recommended production path on docs.openviking.ai.
- Research-donor mode (D) is a waste: the codebase is mature, documented, benchmarked, and already integrated.

The abstraction should treat OV as the **authoritative, durable store** for memories/resources/skills and the session→memory extraction/consolidation engine, while Hermes owns the **confidence-state machine** (CONFIRMED/OBSERVED/INFERRED via extraction result, STALE via hotness decay, CONFLICTED via dedup-diff inspection, UNKNOWN via fallback) and the **soft invalidate/correct/replace** semantics, mapping them onto OV primitives:

| LifeMemory op | OV primitive it compiles to | Confidence-state shim location |
|---|---|---|
| `remember(c)` | `viking_remember` (session → extraction) | — (extraction decides) |
| `recall(q)` / `retrieve_context(…)` | `search(mode="context")` post-filtered by local confidence DB (drop STALE/CONFLICTED) | **Hermes local metadata** |
| `correct(uri,n)` | `write(uri, n)` (file-direct) + set confidence=CONFIRMED | **Hermes local metadata** |
| `invalidate(uri)` | `write` a tombstone flag in local metadata (do NOT `forget`); optionally `mark_stale` | **Hermes local metadata** |
| `mark_stale(uri)` | write `stale=true` in local metadata + bump `updated_at`-independent last-checked | **Hermes local metadata** |
| `resolve_conflict(a,b,c)` | LLM decide survivor via extraction context, `write` survivor, `forget` the loser, set confidence on survivor | **Hermes local metadata** |
| `provenance(uri)` | OV `MemoryOperationSource` + `memory_diff.json` + local confidence/state overlay | **merged** |
| `forget(uri)` | `viking_forget` (permanent) | (terminal) |

This keeps LifeOS on the best available memory engine while owning the confidence-state semantics it was designed around — the exact "LifeOS-owned memory abstraction" posture the brief asks for ("OpenViking MUST sit behind a LifeMemory").

---

*Document generated from live inspection of `volcengine/OpenViking` @ `main` (2026-09-15) and the bundled Hermes `plugins/memory/openviking/__init__.py` (baseline `e12626b3`).*
