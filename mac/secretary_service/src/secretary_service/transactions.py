"""Composable SQLCipher transactions for the domain persistence migration."""

from collections.abc import Generator
from contextlib import contextmanager
from uuid import uuid4

from sqlcipher3 import dbapi2 as sqlcipher


@contextmanager
def domain_transaction(connection: sqlcipher.Connection) -> Generator[None]:
    """Serialize writers; nested operations cannot commit their caller's work.

    A savepoint confines a failed nested operation even when its caller catches
    the exception. The outer transaction owns commit and rollback, including
    failures outside the database driver and cancellation.
    """
    nested = connection.in_transaction
    savepoint = "domain_" + uuid4().hex
    if nested:
        _ = connection.execute(f"SAVEPOINT {savepoint}")
    else:
        _ = connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        if nested:
            _ = connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        else:
            connection.commit()
    except BaseException:
        if nested:
            _ = connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            _ = connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        else:
            connection.rollback()
        raise
