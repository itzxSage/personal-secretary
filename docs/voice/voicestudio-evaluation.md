# VoiceStudio Evaluation — LifeOS Speech Subsystem

> Status: Research evaluation. Source inspected from a shallow clone of `github.com/debpalash/VoiceStudio` (`v0.5.2`, `main`). Findings are source- and doc-grounded; latency figures are the engine's published contract, not independently re-measured on the target box.

## 1. Executive summary

VoiceStudio is a Tauri-v2 desktop voice studio (React + FastAPI backend) with **16 TTS** and **11 ASR** engines, a local OpenAI-compatible audio API, an MCP server, and WebSocket streaming. It is the strongest local, open-source replacement for LifeOS's current `AVSpeechSynthesizer` path: it adds zero-shot voice cloning, an OpenAI-compat `/v1/audio/speech` endpoint (drop-in for the OpenAI client LifeOS already references), true chunked streaming synthesis, and a consent-locked voice-profile model with AudioSeal provenance marking.

**The decisive constraint for this target:** the evaluated hardware is an Intel Mac (`i7-9750H`, 32 GB). VoiceStudio's macOS backend depends on a PyTorch build that **no longer ships Intel-macOS (x86_64) wheels** — the project documents this explicitly (`docs/install/macos.md`, issue `#889`). The Intel DMG installs the UI only; it cannot run the Python backend, and the CosyVoice install button is also disabled on Intel. On this hardware class the only supported configuration is the **Intel Mac as a thin client against a remote backend**, not a local-only backend. The recommendation in §13 reflects that.

## 2. Repository & source overview

```
VoiceStudio/                    v0.5.2 (2026-09-15 shallow clone)
├── frontend/                   React + Vite UI; src-tauri/ = Tauri v2 Rust shell
├── backend/                    FastAPI backend (the real "local speech platform")
│   ├── api/routers/            REST + WebSocket routes
│   │   ├── openai_compat.py    POST /v1/audio/speech, /transcriptions (+WS stream)
│   │   ├── tts_stream.py       WS /ws/tts  ← chunked streaming synthesis
│   │   ├── generation.py       POST /generate (batch; stream=true NDJSON preview)
│   │   ├── mcp_bindings.py     per-agent voice bindings (REST CRUD)
│   │   ├── speech_platform.py  /.well-known/voicestudio-speech discovery + dictation
│   │   ├── capture_ws.py       live transcription WS
│   │   └── capture.py          dictation widget
│   ├── services/               engine registries, model_manager, audio_dsp, watermark
│   └── config/models.yaml      engine metadata
├── docs/                       full docs tree (engines/, install/, specs/, adr/)
├── omnivoice/                  the bundled default TTS package (Apache-2.0 code)
├── deploy/                     docker-compose (cpu/gpu/rocm/worker profiles)
├── scripts/                    bench_pipeline.py, verify-remote-worker.sh, builds
└── pyproject.toml              name=omnivoice, license=AGPL-3.0-only, py>=3.11
```

`main` is tracked as an active beta; releases on the `v*` tags are the stable line. The repo is healthy: CI badge on `main`, `AGENTS.md`/`CLAUDE.md` contributor docs, Alembic SQLite migrations, and an operation-count regression budget in tests (`test_perf_operation_budgets.py`).

## 3. Architecture

```
Tauri v2 shell (Rust)          ← window/tray/shortcuts/sidecar bootstrap
   │ IPC
React + Vite UI                ← Zustand state, API + WS event clients, i18n
   │ HTTP · SSE · WebSocket on localhost:3900
FastAPI backend
   ├── core/  services/        engine registries · model_manager · audio_dsp
   ├── api/routers/            REST + WS (OpenAI-compat · MCP · dictation · streaming)
   └── omnivoice_data/         SQLite (Alembic) + projects, voices, settings, logs
```

- The desktop talks to a **loopback-only** backend on `localhost:3900`.
- Loopback API calls need no key; remote access requires a share PIN or API key (`OMNIVOICE_API_KEY`).
- A **Rust control sidecar** (bundled, `127.0.0.1:3902`) owns mic activation, capture, clipboard safety, and native text insertion — the desktop shell stays the UI, the Python backend stays the data plane.
- Storage is **local-first**: voices, projects, settings, and outputs stay on the machine by default. Network-backed features are explicit opt-ins.

## 4. TTS engines (neural / expressive)

Source: `docs/engines/README.md` + per-engine pages + `README.md` TTS table (16 engines).

| Engine | Neural? | Cloning | Voice design | Languages | Runs on (this Mac) | Model license |
|---|---|---|---|---|---|---|
| **VoiceStudio / OmniVoice (default)** | ✅ neural | ✅ zero-shot | ✅ (attribute instruct) | 600+ | CPU (slow) — no local GPU path on Intel | code Apache-2.0 · **weights CC-BY-NC** |
| CosyVoice 3 | ✅ neural | ✅ | ✅ + natural-language instruct | 9+18 | ❌ Intel unsupported (no PyTorch 2.7 Intel wheel) | Apache-2.0 |
| VoxCPM2 | ✅ neural | ✅ + design | ✅ (in-text prefix) | 30 | CPU (slow) | Apache-2.0 |
| IndexTTS 2.5 | ✅ neural | ✅ + graded emotion | No | ZH/EN/JA/ES/AR | CPU (slow) | Bilibili license |
| GPT-SoVITS | ✅ neural | ✅ (few-shot) | No | 5 | external server | MIT |
| MLX-Audio (Kokoro/CSM/Dia/…) | ✅ neural | varies | varies | varies | ❌ Apple Silicon only | varies |
| KittenTTS | ✅ neural (tiny ONNX) | No (8 presets) | No (EN only) | English | ✅ **CPU** (25–80 MB) | MIT |
| OmniVoice GGUF | ✅ neural (quantized) | ✅ | No (clone only) | 600+ | ✅ CPU (native binary, Q4_K_M) | app AGPL · weights CC-BY-NC |
| PocketTTS (Kyutai) | ✅ neural | ✅ | No | EN/FR/DE/PT/IT/ES | ❌ "not Intel Mac" | CC-BY-4.0 (gated) |
| Supertonic-3 | ✅ neural | No (7 presets) | No | 31 | ✅ CPU | OpenRAIL-M |
| audio.cpp (Breeze-TTS-2) | ✅ neural | ✅ + design | ✅ | — | ✅ CPU (+Vulkan/Metal) | research/non-commercial |
| dots.tts | ✅ neural | ✅ | No | 24 | CPU (slow) | Apache-2.0 |
| Confucius4-TTS | ✅ neural | ✅ | No | 14 | CPU (slow) | Apache-2.0 |
| MOSS-TTS-Nano / v1.5 | ✅ neural | ✅ | No | 20 / 31 | CPU (slow) | Apache-2.0 |

**What this means for the target Intel Mac (i7-9750H, 32 GB, no discrete GPU in most configs):**
- **No CUDA, no MPS** — MPS is Apple-Silicon-only; this is an Intel CPU with integrated Intel UHD 630 (and, on the 16" MBP variants of this era, optionally an AMD Radeon Pro 5300M/5500M, which VoiceStudio does **not** accelerate on macOS — GPU support is CUDA or Apple-Silicon MPS only).
- The **only engines that run locally** on this box would be the pure-CPU ones (KittenTTS, OmniVoice GGUF Q4, Supertonic-3, audio.cpp). The flagship neural/cloning engines (default OmniVoice in-process, CosyVoice, IndexTTS, VoxCPM2) are either slow on CPU or unsupported on Intel.
- The **default OmniVoice path cannot install at all** on Intel macOS because its PyTorch dependency has no Intel-mac wheel.

## 5. First-audio latency & streaming

Two distinct real-time surfaces exist (`backend/api/routers/tts_stream.py`):

**Chunked WebSocket streaming — `/ws/tts` (the conversational path).**
- Protocol: client sends one JSON request `{text, voice, language, speed, instruct, …}`; server streams back **binary PCM16 @ 24 kHz mono** chunks (`CHUNK_SAMPLES = 4800` = 200 ms each) then a JSON `done` frame.
- The request is **sentence-chunked** (`services/sentence_chunker.py`) so the first sentence's audio begins streaming while later sentences are still synthesizing — this is the time-to-first-audio win. Single-sentence requests take the same single-shot path as batch.
- It reports measured latency in the `done` frame: `ttfa_ms` (request→first-audio-bytes), `gen_time_s` (end-to-end wall clock incl. delivery), `rtf` (synthesis seconds ÷ audio seconds, measured around the render only).
- The module docstring sets the **target: <100 ms time-to-first-audio (TTFA) on warm models.**
- The connection stays open for many requests in conversational mode.
- **Routing caveat (by design):** the streaming socket runs **locally** even when a remote GPU worker is configured — "the one surface where latency IS the feature" — so it never farms a streaming utterance to a worker and pays a round trip + cold-load per utterance. (See §12.)
- Remote backends are announced with a `routing`/`local_stream` JSON frame so the client knows it ran locally.

**REST `/generate` (batch).** `stream=true` returns NDJSON sentence preview (base64 PCM per chunk as each finishes), but the comments are explicit: *text-level chunking, no per-engine token streaming*. The on-disk take is assembled through the classic single-shot path, so streaming is purely a delivery channel.

**OpenAI-compatible `POST /v1/audio/speech`.** Returns the **full rendered buffer** at once (`StreamingResponse(io.BytesIO(audio_bytes))`) — the OpenAI SDK's "streaming_response" is just HTTP-body streaming, not chunked synthesis. There is **no** `stream`/`delta` token stream on this endpoint. Agents that need sub-second mouth movements must use `/ws/tts`.

**Latency estimate for "Good morning."** On a **warm** model, the WebSocket path targets **<100 ms TTFA** and delivers the first 200 ms PCM chunk almost immediately (a 3-word phrase is ~0.7 s of audio, fitting in one chunk). Over a LAN to a remote GPU backend, add ~1–3 ms; realistically **≈100–150 ms to first audio, ~0.7 s total**, with the connection held open for the next turn. On a **cold** start add model load (~8 s lazy weights load per `docs/performance.md`). On the **local CPU** path (Intel Mac, no backend support) the phrase would synthesize in ~2–5 s on a small model, so TTFA ≈ synthesis time of the first chunk — acceptable for narration, not for fast turn-taking. Source: `backend/api/routers/tts_stream.py` (docstring + `ttfa_ms`/`rtf` in the `done` frame) and `docs/performance.md`.

## 6. Conversational prosody

Source: `docs/expressive-speech.md` + `docs/specs/01-expressive-tts.md`.

Control is **engine-dependent** and VoiceStudio is unusually honest about what's shipped vs. spec'd:

| Capability | Default engine (OmniVoice) | CosyVoice 3 | IndexTTS2 |
|---|---|---|---|
| `[pause]`, `[pause Nms]` | ✅ all | ✅ all | ✅ all |
| `[laughter]`, `[sigh]` | ✅ 13 native reaction tags | ✅ | ❌ |
| `[breath]` (on-demand inhale) | ❌ (no token; recipe-only) | ✅ inline | ❌ |
| Whisper delivery | ✅ `whisper` style | ✅ instruct | ❌ |
| Graded emotion (vector/slider) | ❌ | ❌ | ✅ 8-dim + emotion ref clip |
| Natural-language instruct | ✅ attribute vocabulary only | ✅ (Sichuan accent, exhausted, …) | ❌ |
| Seed pinning / reproducibility | ✅ lock profile / pin seed | — | — |

- The bracket tag set (`[laughter]`, `[sigh]`, `[pause]`) works **verbatim** — the normalization pass skips `[…]` spans and the chunker never cuts inside a tag (`services/text_normalization.py`, `services/chunked_tts.py`).
- The **reference clip is itself a performance direction**: zero-shot cloning mirrors the *delivery* of the reference, not just timbre — a flat reference clones flat, an animated one clones animated.
- The reference is **combined with style `instruct`**: when they agree, instruct stabilizes the named attributes; when they conflict, the audio wins.
- Free-form design prose is mapped onto a **fixed attribute vocabulary** (Gender/Age/Pitch/Style/Accent/Dialect) and out-of-vocabulary wording is ignored; for description-driven design in other languages, VoxCPM2 is suggested.
- An engine-agnostic `[excited]`/`[whispers]`/`[breath]` tag surface is **spec'd but not shipped** (`specs/01-expressive-tts.md`).

**For LifeOS:** prosody is real but not "ElevenLabs-grade emotion dial" — it's reaction tags + instruct + seed pinning + reference-clip delivery. CosyVoice 3 is the only engine with on-demand `[breath]`, and it's **unsupported on the Intel Mac**. The default engine is the practical baseline.

## 7. Voice cloning & design

Source: `docs/voice-design.md`, `docs/engines/omnivoice.md`, README "First voice" + design docs.

**Cloning (zero-shot).** Default OmniVoice / OmniVoice GGUF / CosyVoice / VoxCPM2 / IndexTTS2 / GPT-SoVITS / dots.tts / Confucius / MOSS-TTS. Reference clip: **3 s works, 5–15 s recommended**; a transcript improves conditioning. Long-clip handling: auto-transcription runs once and is saved to the profile (v0.3.15+); otherwise the app searches up to 75 s of clip in bounded transcription passes. Encoded references are cached on disk (`prompt_cache/`, ~10 KB per voice, 32 newest) so restart doesn't re-encode.

**Voice design (no reference audio).** Default engine attribute mapper (Gender/Age/Pitch/Style/Accent/Dialect, EN/中/混合) and VoxCPM2 (in-text prefix). CosyVoice adds natural-language instruct. No free-form description engine.

**Voice profiles.** Persisted as a profile_id (UUID-like) with `ref_audio`, `ref_text`, `instruct`, `seed`, language. Locked (consent-verified) profiles gate the heavier agentic features (`docs/agentic-voice.md`). The OpenAI-compat and MCP APIs resolve `voice` to a profile_id.

## 8. Intel Mac compatibility (this target: i7-9750H / 32 GB)

This is the gating finding. Two independent facts combine to rule out a local-only deployment:

1. **`docs/install/macos.md`:** "Intel Macs are not supported. The app UI installs and launches, but the **local Python backend cannot run**: PyTorch stopped shipping Intel-Mac (macOS x86_64) wheels after 2.2.x, and VoiceStudio's dependencies require a newer torch." The `x64.dmg` is explicitly "UI only." The app detects this at first launch and says so.
2. **`docs/engines/cosyvoice.md`:** the CosyVoice install button "is not offered on Intel Macs, where PyTorch 2.7.0 has no build."

The i7-9750H is a 6-core/12-thread Coffee Lake CPU. The 16" MBP of that generation shipped with integrated Intel UHD 630 and an optional AMD Radeon Pro 5300M/5500M. **Neither is a VoiceStudio acceleration target** — GPU support is CUDA (NVIDIA/Linux) or Apple-Silicon MPS; AMD and Intel GPUs on macOS fall back to CPU. So even if the backend could install, this box would synthesize on CPU only.

| Engine | Could run here (CPU) | Usable for LifeOS? |
|---|---|---|
| Default OmniVoice (in-process) | ❌ cannot install (no torch wheel) | No |
| OmniVoice GGUF (Q4) | ✅ CPU native binary | Yes — cloning, but ≈10–30× slower than a GPU; high TTFA |
| CosyVoice 3 | ❌ Intel unsupported | No |
| KittenTTS | ✅ CPU (80 MB ONNX) | Yes for EN narration, but no cloning, 8 presets, no emotion |
| IndexTTS/VoxCPM2/MOSS/GPT-SoVITS | ❌ install fails / external | No |

**Conclusion**: a truly local Intel-Mac backend is not viable for lifelike, cloned, conversational speech. The supported pattern is **Intel Mac = thin client → remote backend** (see §12).

## 9. CPU / memory requirements

Source: `README.md` Requirements + Hardware-recommendations + `docs/performance.md` + per-engine pages.

| Resource | Minimum | Recommended (flagship engines) |
|---|---|---|
| OS | macOS 13.3 Apple Silicon · Win 10 x64 · Linux x86_64 (glibc 2.39+) | current release |
| RAM | 8 GB | 16 GB+ (32 GB on Apple Silicon materially helps dub throughput) |
| Disk | 10 GB free | 20+ GB SSD (default model ≈ 2.4 GB; CosyVoice 5.4 GB) |
| GPU | optional (CPU mode supported) | NVIDIA CUDA 8 GB+ · Apple Silicon · (AMD/Intel macOS = CPU) |
| VRAM | 4 GB | 8 GB+ (default OmniVoice **6 GB floor**; <6 GB pages to RAM and times out) |
| Python (source) | 3.11+ | 3.11/3.12 |

- Default OmniVoice has a **measured 6 GB VRAM floor**; on 4 GB cards it pages to system RAM and a seconds-long render runs for minutes until the compute budget kills it. On a CPU box the render gets the longer `OMNIVOICE_CPU_GENERATE_TIMEOUT_S` (600 s default).
- Memory guards exist: `services/memory_budget.log_if_low` before a heavy load, `OMNIVOICE_UNIFIED_OFFLOAD_HEADROOM_GB` (default 6) to evict the TTS model on Apple Silicon when transcription needs room, `OMNIVOICE_SINGLE_ENGINE_RESIDENT` to keep several engines warm on 32 GB+ machines.
- The GPU pool is auto-sized (1 worker / 5 GB free VRAM, max 4; MPS/CPU always 1) — do not raise on ≤10 GB cards.

## 10. Interfaces

### 10.1 Local REST / SSE / WebSocket (`localhost:3900`)
- `POST /generate` — TTS (form fields: `text, voice, engine, language, instruct, description, seed, num_step, guidance_scale, speed, denoise, duration, …`) and batch jobs.
- `POST /api/batch/jobs`, `GET /api/batch/jobs/{id}/events` — **SSE** job progress replay with cancel.
- `WS /ws/tts` — chunked streaming synthesis (§5).
- `WS /v1/audio/transcriptions/stream` — live STT (partial/final/summary) from PCM or WebM.
- `GET /.well-known/voicestudio-speech` — machine-readable transport discovery (`voicestudio.speech.v1`) advertising HTTP, WebSocket, MCP, and native dictation-control transports.
- Loopback needs no credential; remote needs PIN/API key.

### 10.2 OpenAI-compatible audio API
Source: `backend/api/routers/openai_compat.py`. Drop-in for any OpenAI audio SDK:

| Endpoint | Purpose | Streaming? |
|---|---|---|
| `POST /v1/audio/speech` | TTS → mp3/opus/aac/flac/wav/pcm. `model`=engine id, `voice`=profile_id/preset, `speed` | ❌ returns full buffer (HTTP-body streaming only) |
| `POST /v1/audio/transcriptions` | STT → json/text/verbose_json/srt/vtt | ❌ |
| `WS /v1/audio/transcriptions/stream` | live PCM/WebM STT | ✅ live partials |
| `GET /v1/audio/voices` | list local profiles + engines (extension) | n/a |

OpenAI aliases accepted and mapped: `tts-1`/`tts-1-hd`→active engine; `alloy/echo/fable/onyx/nova/shimmer`→defaults. `language`, `instruct`, `description`, `num_step`, `guidance_scale`, `seed` are VoiceStudio extensions passed through. This is exactly the surface LifeOS's `text_to_speech` provider abstraction wants: point the OpenAI client at `http://<host>:3900/v1` with `api_key="local"`.

**Gap note:** there is **no** OpenAI-Realtime-style token streaming on `/v1/audio/speech`. True low-latency voice comes from `/ws/tts`, which the OpenAI client libraries do not consume. LifeOS would need a small adapter from its agent loop to the WebSocket for conversational turn-taking.

### 10.3 MCP server
Source: `docs/mcp.md`, `backend/api/routers/mcp_bindings.py`, `backend/mcp_server.py`.

- Mounted on the **running** backend at `http://localhost:3900/mcp` (Streamable HTTP) — nothing extra to start.
- Stdio-only clients use the bundled shim: `python -m backend.mcp_shim` (httpx stdio↔HTTP proxy).
- Tools: `generate_speech` (text→WAV, base64 or file/URL), `clone_voice` (ref audio→profile_id), `transcribe`, `list_voices`/`list_personalities`/`list_languages`, `check_health` (backend status + active GPU device).
- **Per-agent voice binding**: each MCP client sends `X-VoiceStudio-Client-Id`; a `mcp_client_bindings` table maps client→`{profile_id, default_engine, default_personality}`. Resolution: explicit arg → client binding → global default → helpful error. Managed over loopback REST (`/api/mcp/bindings`).
- Security boundary: `OMNIVOICE_MCP_BASE_PATH` confines file reads/writes; `OMNIVOICE_MCP_ALLOWED_HOSTS` for non-localhost clients (trust the LAN/Tailscale, never expose `/mcp` to the open internet — transport is unauthenticated).

## 11. Licensing, model licensing & commercial use

Source: `LICENSE` (AGPL-3.0 full text, 661 lines), `LICENSE-NOTICE.md`, `pyproject.toml` (`license = "AGPL-3.0-only"`), and the model license footnotes in the README TTS table.

**Application:** GNU **Affero** GPL v3.0 (`AGPL-3.0-only`). License notice explicitly states: "You are free to use, copy, modify, and redistribute it. **That includes commercial and internal business use** of the application itself." Obligation: if you modify VoiceStudio and offer the modified version to others over a network, you must also offer the complete corresponding source under AGPL-3.0. **A commercial license is available** (contact `VoiceStudio@palash.dev`) for embedding in a closed-source/proprietary product without the copyleft obligations.

**Models keep their own terms (not relicensed by the app):**
- Default OmniVoice / OmniVoice GGUF: code **Apache-2.0**, pretrained weights **CC-BY-NC** (non-commercial), plus a separate Boson Higgs Audio 2 / Meta Llama community license on the audio tokenizer.
- CosyVoice 3: **Apache-2.0** (code + 5.4 GB weights).
- IndexTTS 2.5: **Bilibili model license** (requires a separate written license above 100M MAU / 1B RMB revenue).
- PocketTTS: **CC-BY-4.0**, gated.
- Supertonic-3: **OpenRAIL-M**.
- audio.cpp/Breeze: research / non-commercial.
- KittenTTS + AudioSeal watermark: **MIT**.

**Practical commercial reading:** the *app* is commercially usable (AGPL + optional commercial license). The *default cloned voice* uses **CC-BY-NC weights**, so commercial use of speech generated from the default model needs either (a) a commercial model (CosyVoice is Apache-2.0; audio.cpp/Breeze are explicitly non-commercial — check), or (b) the app's commercial license plus a commercial-weight engine. VoiceStudio routes this honestly: per-engine license is shown in Model Catalogue before download. For LifeOS, the safe commercial posture is CosyVoice 3 (Apache-2.0) on a remote GPU box — but note CosyVoice is also "Intel-mac unsupported," reinforcing the remote-backend recommendation.

## 12. Cloud / remote deployment

Source: `docs/remote-gpu.md`, `docs/remote-workers.md`, `docs/install/docker.md`, `docs/api-auth.md`.

Three deployment shapes; only two are relevant to an Intel Mac:

1. **Remote backend (whole backend on another box — the Intel-Mac path).** Run VoiceStudio's backend on a GPU box / cloud GPU and point the Tauri UI at it (`Settings → Sharing → Remote backend`). Uses `OMNIVOICE_API_KEY` as a bearer; the key exchanges once for a short-lived session, WebSockets use path-bound single-use tickets. Security: TLS required beyond a trusted LAN (Tailscale Serve / reverse proxy); bearer-over-plain-HTTP is sniffable; never expose on the open internet. Documented recipe: install Tailscale both ends, `uvicorn backend.main:app --host 0.0.0.0 --port 3900` with the key set, reach via MagicDNS URL. Discovery works at the service root `/.well-known/voicestudio-speech`.

2. **Remote workers (farm individual jobs to other GPUs; keep everything local).** Opt-in, off by default. TTS / audiobook / dub-synthesis dispatch to an enrolled worker over TLS with enrolled keys; ASR/dictation/translation stay local. Dictation deliberately always runs on this machine — "latency is the feature." Voice identity parity: the worker receives the full render contract (reference audio + transcript + seed + chunking settings) so a gallery voice doesn't become a different random voice on the worker.

3. **Docker.** `linux/amd64` images only (`:stable`/`:latest`/`:rocm`, per-engine tags, commit SHAs). CPU, NVIDIA (`--gpus all`, needs NVIDIA Container Toolkit), AMD/ROCm profiles, and `worker-gpu`/`worker-rocm` profiles. `docker compose -f deploy/docker-compose.yml --profile <profile> up -d`. Volumes to persist: `omnivoice_data:/app/omnivoice_data` and the HF cache. **Apple Silicon**: Docker images are `linux/amd64` only and can't reach the Apple GPU — the macOS app is the recommended AMD64/Apple-Silicon path, not Docker-in-Emulation. (On an *Intel* Mac, Docker Desktop runs `linux/amd64` containers natively via HVF — so the CPU image *can* run, but CPU-only and slow for the flagship engine; still not the documented recommendation for Intel.)

**Key operational detail:** the streaming `/ws/tts` path is **local-only by design** even when remote workers are configured — a remote streaming render would pay queue + cold-load + round-trip per utterance, which defeats low-TTFA. So conversational latency over a remote backend depends on the backend machine's own GPU, not a farmed worker.

## 13. Recommendation — best candidate + latency + deployment

**Single best practical lifelike candidate for this Intel Mac (i7-9750H / 32 GB): VoiceStudio backend (default OmniVoice) on a remote GPU box, Intel Mac as Tauri thin client.**

Rationale:
- The Intel Mac **cannot run the backend locally** (§8). This isn't a performance preference — it's a hard install blocker (`PyTorch has no Intel-mac wheel`; CosyVoice install is also gated off on Intel). So "local-only VoiceStudio" is a non-option, regardless of CPU cores.
- A remote GPU backend is the project's own supported answer and matches LifeOS's existing architecture preference (LifeOS already models a "text-event relay" with remote workers and a Swift client shell — VoiceStudio's remote-backend mode is a direct analog).
- The default OmniVoice engine gives **zero-shot cloning** (life up `Good morning` from a 3–10 s of the user's voice — directly fixes LifeOS's "clunky AVSpeechSynthesizer" complaint), **conversational prosody** via `[laughter]`/`[sigh]`/`[pause]` tags + whisper + seed pinning, and the **OpenAI-compatible `/v1/audio/speech`** endpoint LifeOS already speaks — zero code change to the provider layer, just repoint `base_url`.
- For strictly commercial output, swap to **CosyVoice 3** (Apache-2.0) on the same remote box — same deployment, cleaner license; downside is it's another ~5.4 GB and also Intel-mac-unsupported (irrelevant if it lives on the remote box).

If a remote GPU box is not an option and some speech must come from the laptop itself, the only local fallback is **OmniVoice GGUF (Q4_K_M) CPU** for cloning — but expect second-scale turns, not conversational latency.

**Recommended deployment location: remote.** Run `uvicorn backend.main:app --host 0.0.0.0 --port 3900` with an `OMNIVOICE_API_KEY` on a LAN/Tailscale GPU box (an Apple Silicon mini with MLP backends, or any CUDA/ROCm Linux box) and set **Settings → Sharing → Remote backend** to its MagicDNS URL in the Mac's Tauri app. Do **not** expose to the open internet; keep it on a tailnet/reverse-proxy with TLS. Point LifeOS's OpenAI audio client at `http://<gpu-box>:3900/v1`, and route conversational turns through `WS /ws/tts` (OpenAI compat has no token stream).

## 14. Fit against LifeOS goals

| LifeOS need | VoiceStudio provides | Caveat |
|---|---|---|
| Replace AVSpeechSynthesizer with a neural, cloned voice | ✅ OmniVoice zero-shot clone (3–10 s ref), OpenAI `/v1/audio/speech` | Default model is CC-BY-NC; use CosyVoice (Apache-2.0) for commercial |
| OpenAI-compatible audio endpoint | ✅ `POST /v1/audio/speech` (+`/transcriptions`, `/voices`) | No token-streaming on speech; use `/ws/tts` for <100 ms turn latency |
| MCP interface for agents | ✅ mounted `/mcp` + stdio shim, tools: speak/clone/transcribe/list + per-agent voice binding | transport is unauthenticated — LAN/Tailscale only, never public |
| Local-first / private | ✅ local by default; opt-in network is explicit | Remote-backend deployment means audio leaves the laptop over TLS to your box — still your box, not a provider's |
| Conversational prosody | ✅ reaction tags, pauses, whisper, instruct, seed pinning | No graded emotion on default engine; `[breath]` needs CosyVoice (Apache-2.0) |
| CPU/memory bounded | ✅ CPU engines (KittenTTS/OmniVoice GGUF/Supertonic) + budgets/timeouts + flush | Flagship cloning engines are GPU-speed; CPU is 10–30× slower |
| Voice cloning & design | ✅ zero-shot clone + attribute voice design (no ref) | Voice-design is a fixed attribute vocabulary, not free text |

## 15. Gaps & risks for LifeOS integration

- **No Intel-Mac local backend.** Hard blocker, not a perf issue. The Mac becomes a thin client; LifeOS must accept a network hop to the GPU box.
- **OpenAI speech endpoint is not streaming-synthesis.** LifeOS gets batched audio, not incremental chunks. The WebSocket `/ws/tts` gives sub-100 ms TTFA but isn't an OpenAI-client shape — a small adapter is needed for fast turn-taking.
- **AGPL + CC-BY-NC default model.** The app is commercially usable, but the default clone uses non-commercial weights; commercial LifeOS speech should pin CosyVoice (Apache-2.0). The app itself is also AGPL — distributing LifeOS as closed-source around a modified VoiceStudio backend triggers copyleft unless a commercial license is obtained.
- **macOS GPU surface is Apple-Silicon-only.** AMD Radeon on the Intel MBP is unused; CUDA is NVIDIA/Linux-only. The i7-9750H's GPU acceleration ceiling is essentially zero for VoiceStudio.
- **CPU cloning is slow.** OmniVoice GGUF on the 6-core i7 is "latency-tolerable," not conversational — fine for narration, not for agentic back-and-forth.
- **/mcp transport is unauthenticated** by design; security is "trusted LAN/tailnet only." LifeOS must enforce that boundary and not expose it.
- **First-run model download** (~2.4 GB default; 5.4 GB CosyVoice) on the remote box — provision ahead of time.

## 16. Evidence / file references

- Repository: `github.com/debpalash/VoiceStudio`, `main` / `v0.5.2` (shallow clone inspected at `/tmp/voicestudio`).
- App license: `LICENSE` (AGPL-3.0 verbatim), `LICENSE-NOTICE.md` (plain-language summary), `pyproject.toml` (`license = "AGPL-3.0-only"`).
- Intel-Mac blocker: `docs/install/macos.md` ("Intel Macs are not supported"; PyTorch 2.2.x wheel cutoff, issue `#889`).
- Engines: `docs/engines/README.md`, `docs/engines/omnivoice.md`, `docs/engines/kittentts.md`, `docs/engines/omnivoice-gguf.md`, `docs/engines/cosyvoice.md`.
- Latency/streaming: `backend/api/routers/tts_stream.py` (docstring "<100 ms TTFA"; `ttfa_ms`/`rtf` in `done` frame), `docs/performance.md`.
- OpenAI-compat API: `backend/api/routers/openai_compat.py`, `docs/agentic-voice.md`.
- MCP: `docs/mcp.md`, `backend/api/routers/mcp_bindings.py`.
- Remote deployment: `docs/remote-gpu.md`, `docs/remote-workers.md`, `docs/install/docker.md`, `docs/api-auth.md`.
- Prosody/cloning: `docs/expressive-speech.md`, `docs/voice-design.md`, `docs/specs/01-expressive-tts.md`.
- Competitive licensing: `docs/competitive-analysis.md` (license ground rules, §R1 agentic guardrails).
