#!/usr/bin/env python3
"""Scan tracked and untracked, nonignored project files without printing secrets.

This is a high-confidence pattern scan, not a claim of exhaustive secret detection.
Ignored databases, local environment files, build products and reports are outside
the source scan. No network requests or file modifications are performed.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"),
    "aws-access-key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "github-token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{60,})\b"),
    "openai-token": re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{40,}\b"),
    "google-api-key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "slack-token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
}


def source_files(root: Path) -> list[Path]:
    """Include pending user files, even before the repository's first commit."""
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],  # noqa: S607
        cwd=root,
        check=True,
        capture_output=True,
    )
    return sorted({root / name for name in result.stdout.decode().split("\0") if name})


def findings_for_text(text: str) -> list[dict[str, str | int]]:
    """Return rule and line numbers only; never return the matching value."""
    return [
        {"rule": name, "line": text.count("\n", 0, match.start()) + 1}
        for name, pattern in PATTERNS.items()
        for match in pattern.finditer(text)
    ]


def main() -> int:
    """Fail on detected credentials or files that cannot be inspected."""
    findings: list[dict[str, str | int]] = []
    inspected = 0
    for path in source_files(ROOT):
        if path.is_symlink():
            findings.append({"path": str(path.relative_to(ROOT)), "rule": "unscanned-symlink"})
            continue
        try:
            content = path.read_bytes()
        except OSError:
            findings.append({"path": str(path.relative_to(ROOT)), "rule": "unreadable-source"})
            continue
        inspected += 1
        findings.extend(
            {"path": str(path.relative_to(ROOT)), **finding}
            for finding in findings_for_text(content.decode("utf-8", errors="replace"))
        )
    print(json.dumps({"files_scanned": inspected, "findings": findings}, indent=2))
    return int(bool(findings))


if __name__ == "__main__":
    sys.exit(main())
