from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from cryptography.exceptions import InvalidTag
from pydantic import SecretBytes

from secretary_service.audit import AuditTamperError
from secretary_service.cloud_crypto import CloudCipher, CloudStateKeys
from secretary_service.models import RecordKind
from secretary_service.postgres_store import CellMismatchError, PostgresStateStore
from tests.helpers import FakeClock
from tests.postgres_support import PostgresCase
from tests.test_domain_transactions import source
from tests.test_encrypted_state_audit import context


def test_tenant_mismatch_denies_open_and_provision(pg_case: PostgresCase) -> None:
    with pytest.raises(CellMismatchError):
        _ = PostgresStateStore.connect(pg_case.dsn, uuid4(), pg_case.keys)
    with pytest.raises(CellMismatchError):
        PostgresStateStore.provision(pg_case.dsn, uuid4(), pg_case.keys)
    with pg_case.open() as store:
        assert store.verify_audit_chain().entries_verified == 0


def test_no_partial_visibility_and_durable_commit(pg_case: PostgresCase, clock: FakeClock) -> None:
    item = source(clock)
    with pg_case.open() as writer, psycopg.connect(pg_case.dsn, autocommit=True) as observer:
        with writer.domain_transaction() as records:
            records.create(item, context(clock, "test", "isolation"))
            assert observer.execute("SELECT count(*) FROM lifeos_domain").fetchone() == (0,)
            assert observer.execute("SELECT count(*) FROM lifeos_audit").fetchone() == (0,)
        assert observer.execute("SELECT count(*) FROM lifeos_domain").fetchone() == (1,)
    with pg_case.open() as reopened, reopened.domain_transaction() as records:
        assert records.read(RecordKind.SOURCE_ITEM, item.record_id) == item


def test_concurrent_writers_preserve_versions_and_audit(
    pg_case: PostgresCase, clock: FakeClock
) -> None:
    item = source(clock)
    with pg_case.open() as store, store.domain_transaction() as records:
        records.create(item, context(clock, "test", "initial"))
    barrier = Barrier(4)

    def transition(index: int) -> None:
        with pg_case.open() as store:
            _ = barrier.wait(timeout=10)
            with store.domain_transaction() as records:
                records.transition(
                    RecordKind.SOURCE_ITEM,
                    item.record_id,
                    f"version-{index}",
                    context(clock, "test", f"writer-{index}"),
                )

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert list(executor.map(transition, range(4))) == [None] * 4
    with pg_case.open() as store:
        assert store.verify_audit_chain().entries_verified == 5
    with psycopg.connect(pg_case.dsn) as connection:
        assert connection.execute(
            "SELECT version FROM lifeos_domain ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,), (4,), (5,)]


def test_plaintext_absent_and_wrong_data_key_rejected(
    pg_case: PostgresCase, clock: FakeClock
) -> None:
    item = source(clock)
    with pg_case.open() as store, store.domain_transaction() as records:
        records.create(item, context(clock, "test", "encryption"))
    with psycopg.connect(pg_case.dsn) as connection:
        row = connection.execute("SELECT envelope FROM lifeos_domain").fetchone()
        assert row is not None
        assert item.raw_content not in row[0]
    wrong_keys = CloudStateKeys(SecretBytes(b"x" * 32), pg_case.keys.audit)
    with (
        PostgresStateStore.connect(pg_case.dsn, pg_case.tenant_id, wrong_keys) as store,
        store.domain_transaction() as records,
        pytest.raises(InvalidTag),
    ):
        _ = records.read(RecordKind.SOURCE_ITEM, item.record_id)


def test_audit_tampering_and_tail_loss_block_open(pg_case: PostgresCase, clock: FakeClock) -> None:
    with pg_case.open() as store, store.domain_transaction() as records:
        records.create(source(clock), context(clock, "test", "audit"))
    with psycopg.connect(pg_case.dsn) as connection:
        _ = connection.execute("DROP TRIGGER lifeos_audit_immutable ON lifeos_audit")
        _ = connection.execute("DELETE FROM lifeos_audit")
    with pytest.raises(AuditTamperError):
        _ = pg_case.open()


def test_repository_cannot_escape_transaction(pg_case: PostgresCase, clock: FakeClock) -> None:
    with pg_case.open() as store:
        with store.domain_transaction() as records:
            pass
        with pytest.raises(RuntimeError, match="outside"):
            records.create(source(clock), context(clock, "test", "escaped"))


def test_cipher_binds_tenant_record_and_version() -> None:
    keys = CloudStateKeys(SecretBytes(b"d" * 32), SecretBytes(b"a" * 32))
    cipher = CloudCipher(uuid4(), keys)
    identity = ("source_item", str(uuid4()), "1")
    envelope = cipher.seal("synthetic private content", identity)
    assert cipher.open(envelope, identity) == "synthetic private content"
    with pytest.raises(InvalidTag):
        _ = cipher.open(envelope, (*identity[:-1], "2"))
    with pytest.raises(InvalidTag):
        _ = CloudCipher(uuid4(), keys).open(envelope, identity)
