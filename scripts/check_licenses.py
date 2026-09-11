#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = ["pydantic>=2.11,<3"]
# ///

# --- How to run ---
# 1. Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Check: uv run scripts/check_licenses.py
# 3. Override paths: uv run scripts/check_licenses.py --policy PATH --matrix PATH --lock PATH
# ------------------

"""Validate the Capability Graft Matrix and locked dependencies against the license policy."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Final, assert_never

from pydantic import BaseModel, ConfigDict, ValidationError

if TYPE_CHECKING:
    from collections.abc import Sequence

ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_POLICY: Final = ROOT / "policy" / "licenses.yaml"
DEFAULT_MATRIX: Final = ROOT / "docs" / "capability-graft-matrix.md"
DEFAULT_LOCK: Final = ROOT / "uv.lock"
OWNED_DONOR: Final = "LifeOS (owned)"
OWNED_SHA: Final = "owned"
CODE_TAKING_DECISIONS: Final = frozenset({"import", "integrate", "keep"})
SHA_PATTERN: Final = re.compile(r"[0-9a-f]{7,40}")
MATRIX_COLUMNS: Final = 9


class LicensePolicy(BaseModel):
    """License classes and the transitive package map parsed from the policy file."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    version: int
    allowed: tuple[str, ...]
    review_required: tuple[str, ...]
    prohibited: tuple[str, ...]
    decisions: tuple[str, ...]
    transitive: dict[str, str]


@dataclass(frozen=True, slots=True)
class MatrixEntry:
    """One capability row from the graft matrix."""

    capability: str
    donor: str
    donor_sha: str
    license: str
    decision: str
    owner: str
    update_rule: str
    security_decision: str


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Outcome of a policy check."""

    ok: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CliOptions:
    """Explicit file paths selected on the command line."""

    policy: Path
    matrix: Path
    lock: Path


class CliOption(StrEnum):
    """Command-line options that take a path value."""

    POLICY = "--policy"
    MATRIX = "--matrix"
    LOCK = "--lock"


class LicenseClass(StrEnum):
    """Classification of a license expression against the policy."""

    ALLOWED = "allowed"
    REVIEW_REQUIRED = "review_required"
    PROHIBITED = "prohibited"
    UNKNOWN = "unknown"


class PolicyFileError(Exception):
    """Raised when a policy, matrix, or lock file cannot be parsed."""

    def __init__(self, path: Path, detail: str) -> None:
        """Record the offending path and the parse failure detail."""
        super().__init__(f"{path}: {detail}")
        self.path = path
        self.detail = detail


def load_policy(path: Path) -> LicensePolicy:
    """Parse and validate the license policy file."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise PolicyFileError(path, f"cannot parse policy: {exc}") from exc
    try:
        return LicensePolicy.model_validate(raw)
    except ValidationError as exc:
        raise PolicyFileError(path, f"invalid policy: {exc}") from exc


def parse_matrix(path: Path) -> tuple[MatrixEntry, ...]:
    """Parse capability rows from the graft matrix Markdown table."""
    entries: list[MatrixEntry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) != MATRIX_COLUMNS:
            raise PolicyFileError(
                path, f"matrix row has {len(cells)} columns, expected {MATRIX_COLUMNS}"
            )
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        if cells[0] == "Capability":
            continue
        entries.append(
            MatrixEntry(
                capability=cells[0],
                donor=cells[1],
                donor_sha=cells[2],
                license=cells[3],
                decision=cells[4],
                owner=cells[6],
                update_rule=cells[7],
                security_decision=cells[8],
            )
        )
    if not entries:
        raise PolicyFileError(path, "no matrix rows found")
    return tuple(entries)


def load_locked_packages(path: Path) -> tuple[str, ...]:
    """Extract package names from a uv.lock file."""
    names: list[str] = []
    in_package = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line == "[[package]]":
            in_package = True
            continue
        if in_package and line.startswith("name = "):
            names.append(line.removeprefix('name = "').removesuffix('"'))
            in_package = False
    return tuple(names)


def classify(policy: LicensePolicy, license_id: str) -> LicenseClass:
    """Classify an SPDX license expression, honoring OR alternatives."""
    alternatives = tuple(part.strip() for part in license_id.split(" OR "))
    classes = {_classify_single(policy, alternative) for alternative in alternatives}
    if LicenseClass.ALLOWED in classes:
        return LicenseClass.ALLOWED
    if LicenseClass.PROHIBITED in classes:
        return LicenseClass.PROHIBITED
    if LicenseClass.REVIEW_REQUIRED in classes:
        return LicenseClass.REVIEW_REQUIRED
    return LicenseClass.UNKNOWN


def _classify_single(policy: LicensePolicy, license_id: str) -> LicenseClass:
    if license_id == "proprietary":
        return LicenseClass.ALLOWED
    if license_id in policy.allowed:
        return LicenseClass.ALLOWED
    if license_id in policy.review_required:
        return LicenseClass.REVIEW_REQUIRED
    if license_id in policy.prohibited:
        return LicenseClass.PROHIBITED
    return LicenseClass.UNKNOWN


def check_policy(
    policy: LicensePolicy,
    entries: Sequence[MatrixEntry],
    locked_packages: Sequence[str],
) -> CheckResult:
    """Check matrix entries and locked packages against the policy."""
    errors: list[str] = []
    warnings: list[str] = []
    for entry in entries:
        _check_entry(policy, entry, errors, warnings)
    for package in locked_packages:
        _check_transitive(policy, package, errors, warnings)
    return CheckResult(ok=not errors, errors=tuple(errors), warnings=tuple(warnings))


def _check_entry(
    policy: LicensePolicy,
    entry: MatrixEntry,
    errors: list[str],
    warnings: list[str],
) -> None:
    if entry.donor == OWNED_DONOR:
        if entry.donor_sha != OWNED_SHA:
            errors.append(f"{entry.capability}: owned donor must use sha {OWNED_SHA!r}")
    elif SHA_PATTERN.fullmatch(entry.donor_sha) is None:
        errors.append(
            f"{entry.capability}: donor {entry.donor} is unpinned (sha {entry.donor_sha!r})"
        )
    if entry.decision not in policy.decisions:
        errors.append(f"{entry.capability}: unknown decision {entry.decision!r}")
        return
    if entry.decision in CODE_TAKING_DECISIONS:
        _check_code_taking_license(policy, entry, errors, warnings)
    if not entry.owner.strip():
        errors.append(f"{entry.capability}: missing owner")
    if not entry.update_rule.strip():
        errors.append(f"{entry.capability}: missing update rule")
    if not entry.security_decision.strip():
        errors.append(f"{entry.capability}: missing security decision")


def _check_code_taking_license(
    policy: LicensePolicy,
    entry: MatrixEntry,
    errors: list[str],
    warnings: list[str],
) -> None:
    """Classify the donor license of a code-taking decision."""
    match classify(policy, entry.license):
        case LicenseClass.ALLOWED:
            pass
        case LicenseClass.REVIEW_REQUIRED:
            warnings.append(f"{entry.capability}: review-required license {entry.license}")
        case LicenseClass.PROHIBITED:
            errors.append(
                f"{entry.capability}: prohibited license {entry.license} for {entry.decision}"
            )
        case LicenseClass.UNKNOWN:
            errors.append(
                f"{entry.capability}: unknown license {entry.license!r} for {entry.decision}"
            )
        case _ as unreachable:
            assert_never(unreachable)


def _check_transitive(
    policy: LicensePolicy,
    package: str,
    errors: list[str],
    warnings: list[str],
) -> None:
    license_id = policy.transitive.get(package)
    if license_id is None:
        errors.append(f"transitive {package}: license unknown (not listed in policy.transitive)")
        return
    match classify(policy, license_id):
        case LicenseClass.ALLOWED:
            pass
        case LicenseClass.REVIEW_REQUIRED:
            warnings.append(f"transitive {package}: review-required license {license_id}")
        case LicenseClass.PROHIBITED:
            errors.append(f"transitive {package}: prohibited license {license_id}")
        case LicenseClass.UNKNOWN:
            errors.append(f"transitive {package}: unknown license {license_id!r}")
        case _ as unreachable:
            assert_never(unreachable)


def _parse_cli_options(args: Sequence[str]) -> CliOptions:
    """Parse CLI option/value pairs into explicit paths."""
    policy_path = DEFAULT_POLICY
    matrix_path = DEFAULT_MATRIX
    lock_path = DEFAULT_LOCK
    index = 0
    while index < len(args):
        try:
            option = CliOption(args[index])
        except ValueError:
            detail = f"unexpected argument: {args[index]}"
            raise PolicyFileError(Path("<cli>"), detail) from None
        if index + 1 >= len(args):
            detail = f"missing value for {option.value}"
            raise PolicyFileError(Path("<cli>"), detail)
        value = args[index + 1]
        match option:
            case CliOption.POLICY:
                policy_path = Path(value)
            case CliOption.MATRIX:
                matrix_path = Path(value)
            case CliOption.LOCK:
                lock_path = Path(value)
            case _ as unreachable:
                assert_never(unreachable)
        index += 2
    return CliOptions(policy=policy_path, matrix=matrix_path, lock=lock_path)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the license policy check from the command line."""
    args = list(sys.argv[1:] if argv is None else argv)
    if "--help" in args or "-h" in args:
        print("usage: check_licenses.py [--policy PATH] [--matrix PATH] [--lock PATH]")
        return 0
    try:
        options = _parse_cli_options(args)
        policy = load_policy(options.policy)
        entries = parse_matrix(options.matrix)
        locked_packages = load_locked_packages(options.lock)
    except PolicyFileError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    result = check_policy(policy, entries, locked_packages)
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    for error in result.errors:
        print(f"error: {error}", file=sys.stderr)
    if result.ok:
        print(
            f"license policy ok: {len(entries)} subsystems, {len(locked_packages)} locked packages"
        )
        return 0
    print(f"license policy failed: {len(result.errors)} error(s)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
