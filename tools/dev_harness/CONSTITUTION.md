# Engineering constitution

- Repository/worktree is authoritative. Inspect instructions, source and Git state before editing.
- Never reset, revert, clean, stash, discard work, rewrite history or force-push without explicit authorization.
- Requested ≠ implemented ≠ tested ≠ verified. Queued instructions prove nothing completed.
- Never invent command results or hide failures/skips. Cite runner evidence and its source state.
- Builders cannot grant VERIFIED. Independent review plus machine gates own that status.
- Simulator success cannot certify devices, OAuth interaction, sound, hardware or real-world UX.
- Keep scope bounded by the contract. Stop when prerequisites fail. Two failed verification rounds require escalation.
- Preserve privacy and authority boundaries. No secrets/private state in commits or handoffs, public exposure, paid provisioning, external deletion, or weakening security to pass tests.
- High-risk/security work requires senior review before implementation. Do not invoke paid models automatically.
- After compaction, read the contract, current Git state and latest handoff. Do not rely on remembered completion.
