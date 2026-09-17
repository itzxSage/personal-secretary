# AGENTS.md — personal-secretary / LifeOS

## Trust hierarchy

- Executable sources beat prose. If docs conflict with code/scripts, trust the code.
- Current-state map: `docs/capability-maturity.md` (explicitly: "no row currently establishes production readiness").
- `docs/lifeos-plan.md` and `.omo/plans/` are **historical** — checkmarks are not completion evidence. Never execute their todos literally.

## Commands (use exactly these)

```bash
uv sync --locked --dev          # install; Python 3.13 only, macOS required
uv run pytest -q                # Python suite (default: no external services)
swift test --package-path ios/SecretaryApp     # Swift suite
swift build --package-path ios/SecretaryApp
bash scripts/verify-foundation.sh <run-id>     # full gate, fixed order — see below
uv run scripts/verify_lifeos.py --local-only --run-id <id>   # local gates; report keeps release_ready:false
bash scripts/e2e_lifeos.sh --run-id local-demo  # fixture e2e, no personal accounts
```

Gate order in `verify-foundation.sh` (do not reorder): contracts `--check` → `ruff format --check` → `ruff check` → `basedpyright` → `pytest` → swift test+build → dry-run refusal probe → live health probes (200/426/426).

## Codegen — never hand-edit generated files

- Source of truth: `contracts/v1/*.schema.json`. Regenerate with `uv run scripts/generate_contracts.py`; verify with `--check` (CI enforces it).
- Generated outputs (do not edit): `*.generated.swift` in `ios/.../Contract/`, `mac/.../contract_version.py`, `mac/.../conversation_api.py`.

## Lint / types (strict — CI fails)

- Ruff: `select = ["ALL"]`, line-length 100, pydocstyle google convention. Test files relax `S101/ARG/PLR2004/SLF001/D`.
- Basedpyright `typeCheckingMode = "all"` over `mac/.../src` + `tests`, with `reportPrivateUsage`/`reportUnusedVariable` as errors.
- Pytest: `filterwarnings = ["error"]`, `--strict-markers`. Time is deterministic via `--fake-clock` (default `2026-09-05T14:00:00Z`); crypto via `DeterministicTestKeyProvider` — never use real keys in tests.

## Service / health quirks

- `mac/secretary_service/bin/run` **refuses** `SECRETARY_DRY_RUN=0` (exit 64). Healthy local run: `SECRETARY_DRY_RUN=1`.
- `/health` requires header `X-Secretary-Contract-Version: <current>` → 200; wrong/malformed → 426. Get the version from `secretary_service.contract_version.CONTRACT_VERSION`.
- Two services, neither public: `secretary_service.app:app` (health-only) and `secretary_service.slice.api:app` (fixture calendar demo). Production connectors stay `disabled`.

## Test gating that surprises

- Postgres tests **skip silently** unless `LIFEOS_TEST_POSTGRES_DSN` is set; real PG evidence needs `uv run scripts/verify_postgres.py` (+ `--with-postgres` on the verifier). CI installs `postgresql@17` via Homebrew.
- `verify_lifeos.py` full run returns **nonzero while release requirements are unmet even when local checks pass** — that is expected, not breakage.
- Native iOS QA needs Xcode + paired iPhone (`e2e_lifeos.sh --ios-simulator 'iPhone 15'`, `scripts/ios_device_surface_check.sh`). Simulator-only runs are fine for shared-handler logic.

## Architecture facts that change how you work

- The Mac Python engine owns all canonical state, authority, and audit. iOS/OpenClaw/voice are adapters — they never mutate canonical records directly; deterministic code alone mutates state.
- Planner (`mac/.../planner.py`) is pure/deterministic, minute-resolution. Capability routing is keyword-based (`capabilities/router.py`); the model-classifier protocol is declared but unwired.
- Authority invariant: **voice is a request, never a proof** — consequential actions need device-signed approval + lease + payload hash (`authority.py`).
- Persistence is SQLCipher via `EncryptedStateStore` (migrations `001–009`); every mutation embeds an HMAC audit entry verified before write. Production secrets live in macOS Keychain service `com.personal-secretary.service`.
- Memory has three coexisting backends behind the `LifeMemory` protocol (JSONL canonical, SQLCipher repository, additive OpenViking vector store) — don't "consolidate" them unasked.

## Hard boundaries

- No autonomous money/destructive/irreversible actions; no general browser/computer workers (explicitly gated out).
- `scripts/scan_secrets.py` prints rule/path/line only — never credential values. Keep it that way.
- Reports go in `artifacts/verification/<run-id>/` with a fresh run ID per verification; don't overwrite prior evidence.
- Full Xcode + paired iPhone required for native QA; Google OAuth (`scripts/google_calendar_oauth.py`) needs interactive browser consent — both are human-gated, don't attempt headless.
