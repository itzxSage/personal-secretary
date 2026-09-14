"""Run real PostgreSQL tests in an owner-only temporary Unix-socket cluster.

Usage: uv run scripts/verify_postgres.py [--bin-dir /path/to/postgresql/bin]
No TCP listener or launch service is created. Only synthetic test data is used.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


def postgres_bin(explicit: str | None) -> Path:
    """Locate binaries from an explicit directory, PATH, Homebrew, or apt."""
    if explicit is not None:
        return Path(explicit).resolve()
    found = shutil.which("pg_ctl")
    if found:
        return Path(found).resolve().parent
    candidates = [
        Path("/usr/local/opt/postgresql@17/bin"),
        Path("/opt/homebrew/opt/postgresql@17/bin"),
        Path("/usr/lib/postgresql/17/bin"),
        Path("/usr/lib/postgresql/16/bin"),
    ]
    for candidate in candidates:
        if (candidate / "pg_ctl").is_file():
            return candidate
    message = "PostgreSQL binaries missing; install PostgreSQL or supply --bin-dir"
    raise FileNotFoundError(message)


def run() -> int:
    """Start isolated PostgreSQL, run contract tests, and always stop the server."""
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--bin-dir")
    _ = parser.add_argument("--junitxml", type=Path)
    args = parser.parse_args()
    binary = postgres_bin(args.bin_dir)
    root = Path(__file__).resolve().parents[1]
    with TemporaryDirectory(prefix="lifeos-pg-", dir="/tmp") as temporary:
        cluster = Path(temporary)
        socket = cluster / "socket"
        socket.mkdir(mode=0o700)
        data = cluster / "data"
        _ = subprocess.run(  # noqa: S603 -- local PostgreSQL binaries and synthetic paths
            [
                str(binary / "initdb"),
                "-D",
                str(data),
                "-U",
                "lifeos_test",
                "--auth-local=trust",
                "--auth-host=reject",
                "--encoding=UTF8",
                "--no-locale",
            ],
            check=True,
            capture_output=True,
        )
        control = [str(binary / "pg_ctl"), "-D", str(data)]
        try:
            _ = subprocess.run(  # noqa: S603 -- Unix socket only, no external credentials
                [
                    *control,
                    "-l",
                    str(cluster / "postgres.log"),
                    "-o",
                    f"-k {socket} -h '' -p 55439",
                    "-w",
                    "start",
                ],
                check=True,
            )
            environment = dict(os.environ)
            environment["LIFEOS_TEST_POSTGRES_DSN"] = (
                f"host={socket} port=55439 user=lifeos_test dbname=postgres"
            )
            command = [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/test_postgres_store.py",
                "tests/test_persistence_contract.py",
                "tests/test_postgres_outbox.py",
                "tests/test_postgres_conversations.py",
            ]
            if args.junitxml is not None:
                command.append(f"--junitxml={args.junitxml}")
            result = subprocess.run(  # noqa: S603 -- repository test entry point
                command,
                cwd=root,
                env=environment,
                check=False,
            )
            return result.returncode
        finally:
            _ = subprocess.run(  # noqa: S603 -- stop only the cluster this script created
                [*control, "-m", "immediate", "-w", "stop"],
                check=False,
            )


if __name__ == "__main__":
    raise SystemExit(run())
