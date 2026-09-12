# Capability Graft Matrix

Every subsystem of the LifeOS architecture records its donor, the pinned donor
revision, the donor license, the reuse decision, what we take, the owner, the
update rule, and the security decision. External donors are consumed only at a
pinned revision through adapters or clean-room reimplementation; owned
subsystems are built inside the Life Engine boundary and carry no external
license obligations.

| Capability | Donor | Donor SHA | License | Decision | What we take | Owner | Update rule | Security decision |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Agent gateway | OpenClaw | befc0c24 | MIT | keep | Long-running Gateway, WS control plane | Jared Gagne | pin updates | adapter-only |
| Optional planning agent | Hermes Agent | 2237be355906fbe6065ce1815711eee52b2d646e | MIT | integrate | HTTP planning proposals through the existing interpretation interface | Jared Gagne | pinned candidate; compatibility and isolation review before upgrades | isolated advisory-only; no canonical state or external actions |
| Messaging | OpenClaw | befc0c24 | MIT | keep | WhatsApp, Telegram, Slack, Discord, Signal, iMessage/WebChat | Jared Gagne | pin updates | adapter-only |
| Mobile/device nodes | OpenClaw | befc0c24 | MIT | keep | iOS/Android/macOS/headless node model | Jared Gagne | pin updates | adapter-only |
| Model/runtime abstraction | OpenClaw | befc0c24 | MIT | keep | Provider/model/runtime separation | Jared Gagne | pin updates | adapter-only |
| Codex runtime | OpenClaw | befc0c24 | MIT | keep | Native Codex harness | Jared Gagne | pin updates | adapter-only |
| Plugin architecture | OpenClaw | befc0c24 | MIT | keep | Runtime plugins + plugin SDK | Jared Gagne | pin updates | adapter-only |
| Multi-agent delegation | LifeOS (owned) | owned | proprietary | build | Isolated specialist agents | Jared Gagne | owned | owned |
| User profile | LifeOS (owned) | owned | proprietary | build | Dedicated durable user model | Jared Gagne | owned | owned |
| Agent memory | LifeOS (owned) | owned | proprietary | build | Curated + deep long-term memory | Jared Gagne | owned | owned |
| Procedural learning | LifeOS (owned) | owned | proprietary | build | Agent-created skills | Jared Gagne | owned | owned |
| Self-improvement loop | LifeOS (owned) | owned | proprietary | build | Review interactions and learn | Jared Gagne | owned | owned |
| Scheduling tasks | LifeOS (owned) | owned | proprietary | reimplement | Priority/deadline/dependency scheduling | Jared Gagne | owned | owned |
| Flexible calendar | LifeOS (owned) | owned | proprietary | reimplement | Habits, buffers, protected time, conflict resolution | Jared Gagne | owned | owned |
| Calendar preview | LifeOS (owned) | owned | proprietary | reimplement | Stage schedule changes before committing | Jared Gagne | owned | owned |
| Browser autonomy | browser-use | e25ab65 | MIT | integrate | Persistent autonomous browser worker | Jared Gagne | pin updates | sandboxed |
| Computer control | Open Interpreter | 7f0fd99 | Apache-2.0 | integrate | Files/apps/native computer execution | Jared Gagne | pin updates | sandboxed |
| Email executive-assistant behavior | LifeOS (owned) | owned | proprietary | reimplement | Thread handling, follow-ups, prep | Jared Gagne | owned | owned |
| Workflow/event automation | n8n | 33eb5c1 | Sustainable Use License | reimplement | Deterministic event-based automation | Jared Gagne | pin updates | informational |
| Documents | LifeOS (owned) | owned | proprietary | build | Docs/Sheets/files creation and editing | Jared Gagne | owned | owned |
| Goal planning | LifeOS (owned) | owned | proprietary | build | Dreams to goals to quests to tasks | Jared Gagne | owned | owned |
| Priority engine | LifeOS (owned) | owned | proprietary | build | Life-goal-aware ranking | Jared Gagne | owned | owned |
| Energy model | LifeOS (owned) | owned | proprietary | build | Predict best work from historical state | Jared Gagne | owned | owned |
| Life replanning | LifeOS (owned) | owned | proprietary | build | Recalculate entire day as state changes | Jared Gagne | owned | owned |
| Delegation engine | LifeOS (owned) | owned | proprietary | build | Decide Jared vs agent | Jared Gagne | owned | owned |
| Authority system | LifeOS (owned) | owned | proprietary | build | Observe/recommend/act/external-action boundaries | Jared Gagne | owned | owned |

## Provenance rules

- External donors are pinned by commit SHA and consumed through versioned
  adapters or clean-room reimplementation; OpenClaw core is never modified.
- Code-taking decisions (keep, integrate, import) require an allowed or
  review-required license. Reference, reimplement, build, and ignore decisions
  treat the donor license as informational.
- n8n is reimplemented, not integrated, because its Sustainable Use License
  restricts commercial reuse.
- Conceptual donors without a pinned revision (Hermes, Motion, Reclaim, Lindy,
  Letta, Google APIs) are recorded as owned clean-room builds inside the Life
  Engine boundary.
- Transitive Python dependencies are licensed per `policy/licenses.yaml` and
  verified against `uv.lock` by `scripts/check_licenses.py`.
