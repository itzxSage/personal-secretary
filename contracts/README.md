# Contracts

`v1/secretary-api.schema.json` is the sole source of truth for the transport contract
version. Regenerate checked-in Python and Swift constants after changing it:

```bash
uv run scripts/generate_contracts.py
```

CI uses `--check` to reject stale generated sources. Contract directory names are
immutable API generations; incompatible changes require a new directory.
