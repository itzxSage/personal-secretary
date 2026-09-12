"""Synthetic PostgreSQL cells; no external database is contacted by default."""

import os
from collections.abc import Generator
from dataclasses import dataclass
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from pydantic import SecretBytes

from secretary_service.cloud_crypto import CloudStateKeys
from secretary_service.postgres_store import PostgresStateStore


@dataclass(frozen=True)
class PostgresCase:
    dsn: str
    tenant_id: UUID
    keys: CloudStateKeys

    def open(self) -> PostgresStateStore:
        return PostgresStateStore.connect(self.dsn, self.tenant_id, self.keys)


@pytest.fixture
def pg_case() -> Generator[PostgresCase]:
    dsn = os.environ.get("LIFEOS_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("run uv run scripts/verify_postgres.py for real PostgreSQL evidence")
    schema = "lifeos_test_" + uuid4().hex
    keys = CloudStateKeys(SecretBytes(os.urandom(32)), SecretBytes(os.urandom(32)))
    with psycopg.connect(dsn, autocommit=True) as connection:
        _ = connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            case = PostgresCase(
                make_conninfo(dsn, options=f"-csearch_path={schema}"), uuid4(), keys
            )
            PostgresStateStore.provision(case.dsn, case.tenant_id, keys)
            yield case
        finally:
            # This fixture owns exactly this freshly generated synthetic schema.
            _ = connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
