from __future__ import annotations

import json
from pathlib import Path
from typing import Final

import pytest
from scripts.check_licenses import (
    PolicyFileError,
    check_policy,
    load_locked_packages,
    load_policy,
    parse_matrix,
)

ROOT: Final = Path(__file__).resolve().parents[1]

MATRIX_HEADER: Final = [
    "Capability",
    "Donor",
    "Donor SHA",
    "License",
    "Decision",
    "What we take",
    "Owner",
    "Update rule",
    "Security decision",
]


def _policy_json(*, transitive: dict[str, str] | None = None) -> str:
    policy = {
        "version": 1,
        "allowed": ["MIT", "Apache-2.0"],
        "review_required": ["LGPL-2.1-only", "LGPL-3.0-only", "MPL-2.0"],
        "prohibited": ["GPL-2.0-only", "GPL-3.0-only", "AGPL-3.0-only", "Sustainable Use License"],
        "decisions": ["reference", "import", "reimplement", "build", "integrate", "keep", "ignore"],
        "transitive": transitive or {"demo-pkg": "MIT"},
    }
    return json.dumps(policy)


def _write_policy(tmp_path: Path, *, transitive: dict[str, str] | None = None) -> Path:
    path = tmp_path / "licenses.yaml"
    _ = path.write_text(_policy_json(transitive=transitive), encoding="utf-8")
    return path


def _write_matrix(tmp_path: Path, rows: list[list[str]]) -> Path:
    header = "| " + " | ".join(MATRIX_HEADER) + " |"
    separator = "|" + "|".join(["---"] * len(MATRIX_HEADER)) + "|"
    lines = [header, separator]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    path = tmp_path / "capability-graft-matrix.md"
    _ = path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_lock(tmp_path: Path, packages: list[str]) -> Path:
    lines = ["version = 1", "revision = 3", 'requires-python = "==3.13.*"', ""]
    for package in packages:
        lines.extend(["[[package]]", f'name = "{package}"', 'version = "0.0.0"', ""])
    path = tmp_path / "uv.lock"
    _ = path.write_text("\n".join(lines), encoding="utf-8")
    return path


def test_existing_python_provenance_uses_registry_artifact_hashes() -> None:
    # Given: the foundation's current Python dependency lock.
    lock_text = (ROOT / "uv.lock").read_text(encoding="utf-8")

    # When: its observable provenance markers are inspected.
    uses_registry_sources = 'source = { registry = "https://pypi.org/simple" }' in lock_text
    records_artifact_hashes = 'hash = "sha256:' in lock_text

    # Then: dependencies remain registry-sourced and content-addressed.
    assert uses_registry_sources
    assert records_artifact_hashes
    assert "git+" not in lock_text


def test_unpinned_donor_entry_is_rejected(tmp_path: Path) -> None:
    # Given: a policy and a matrix whose external donor has no pinned SHA.
    policy = load_policy(_write_policy(tmp_path))
    matrix = parse_matrix(
        _write_matrix(
            tmp_path,
            [
                [
                    "Agent gateway",
                    "OpenClaw",
                    "",
                    "MIT",
                    "keep",
                    "Gateway",
                    "Jared Gagne",
                    "pin updates",
                    "adapter-only",
                ]
            ],
        )
    )

    # When: the policy is checked against the matrix.
    result = check_policy(policy, matrix, ())

    # Then: the unpinned donor entry is rejected.
    assert not result.ok
    assert any("unpinned" in error for error in result.errors)


def test_agpl_donor_entry_is_rejected(tmp_path: Path) -> None:
    # Given: a policy and a matrix that imports AGPL donor code.
    policy = load_policy(_write_policy(tmp_path))
    matrix = parse_matrix(
        _write_matrix(
            tmp_path,
            [
                [
                    "Agent memory",
                    "SomeDonor",
                    "0123456789abcdef0123456789abcdef01234567",
                    "AGPL-3.0-only",
                    "import",
                    "memory",
                    "Jared Gagne",
                    "pin updates",
                    "isolated",
                ]
            ],
        )
    )

    # When: the policy is checked against the matrix.
    result = check_policy(policy, matrix, ())

    # Then: the AGPL donor entry is rejected.
    assert not result.ok
    assert any("AGPL-3.0-only" in error for error in result.errors)


def test_unknown_transitive_license_is_rejected(tmp_path: Path) -> None:
    # Given: a policy whose transitive map omits one locked package.
    policy = load_policy(_write_policy(tmp_path, transitive={"known-pkg": "MIT"}))
    locked = load_locked_packages(_write_lock(tmp_path, ["known-pkg", "mystery-pkg"]))

    # When: the policy is checked against the locked packages.
    result = check_policy(policy, (), locked)

    # Then: the unknown transitive license is rejected.
    assert not result.ok
    assert any("mystery-pkg" in error for error in result.errors)


def test_incompatible_transitive_license_is_rejected(tmp_path: Path) -> None:
    # Given: a policy whose transitive map records a GPL package.
    policy = load_policy(_write_policy(tmp_path, transitive={"bad-pkg": "GPL-3.0-only"}))
    locked = load_locked_packages(_write_lock(tmp_path, ["bad-pkg"]))

    # When: the policy is checked against the locked packages.
    result = check_policy(policy, (), locked)

    # Then: the incompatible transitive license is rejected.
    assert not result.ok
    assert any("GPL-3.0-only" in error for error in result.errors)


def test_malformed_policy_is_rejected(tmp_path: Path) -> None:
    # Given: a policy file that is not parseable JSON-compatible YAML.
    path = tmp_path / "licenses.yaml"
    _ = path.write_text("{not json", encoding="utf-8")

    # When: the policy is loaded.
    # Then: a typed parse error is raised.
    with pytest.raises(PolicyFileError):
        _ = load_policy(path)


def test_repository_policy_matrix_and_lock_pass() -> None:
    # Given: the repository's checked-in policy, matrix, and lock.
    policy = load_policy(ROOT / "policy" / "licenses.yaml")
    matrix = parse_matrix(ROOT / "docs" / "capability-graft-matrix.md")
    locked = load_locked_packages(ROOT / "uv.lock")

    # When: the policy is checked against both.
    result = check_policy(policy, matrix, locked)

    # Then: every subsystem and locked package is compliant.
    assert result.ok, result.errors
