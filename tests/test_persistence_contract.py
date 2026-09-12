from collections.abc import Generator
from typing import cast

import psycopg
import pytest
from pydantic import ValidationError
from sqlcipher3 import dbapi2 as sqlcipher

from secretary_service.models import RecordKind
from secretary_service.postgres_store import PostgresStateStore
from secretary_service.storage import EncryptedStateStore
from tests.helpers import FakeClock
from tests.postgres_support import PostgresCase
from tests.test_domain_transactions import source
from tests.test_encrypted_state_audit import context


@pytest.fixture(params=["sqlcipher", "postgres"])
def persistence_state(
    request: pytest.FixtureRequest,
) -> Generator[EncryptedStateStore | PostgresStateStore]:
    if cast("str", request.param) == "sqlcipher":
        state = cast("EncryptedStateStore", request.getfixturevalue("store"))
        assert isinstance(state, EncryptedStateStore)
        yield state
    else:
        case = cast("PostgresCase", request.getfixturevalue("pg_case"))
        assert isinstance(case, PostgresCase)
        with case.open() as state:
            yield state


def test_domain_lifecycle_contract(
    persistence_state: EncryptedStateStore | PostgresStateStore, clock: FakeClock
) -> None:
    item = source(clock)
    ctx = context(clock, "contract", "lifecycle")
    with persistence_state.domain_transaction() as records:
        records.create(item, ctx)
        records.transition(RecordKind.SOURCE_ITEM, item.record_id, "processed", ctx)
        changed = records.read(RecordKind.SOURCE_ITEM, item.record_id)
        assert changed is not None
        assert changed.state == "processed"
    with persistence_state.domain_transaction() as records:
        records.delete(RecordKind.SOURCE_ITEM, item.record_id, ctx)
        assert records.read(RecordKind.SOURCE_ITEM, item.record_id) is None
    assert persistence_state.verify_audit_chain().entries_verified == 3


def test_failed_batch_contract(
    persistence_state: EncryptedStateStore | PostgresStateStore, clock: FakeClock
) -> None:
    item = source(clock)

    def fail_batch() -> None:
        with persistence_state.domain_transaction() as records:
            records.create(item, context(clock, "contract", "rollback"))
            records.create(item, context(clock, "contract", "duplicate"))

    with pytest.raises((psycopg.IntegrityError, sqlcipher.IntegrityError)):
        fail_batch()
    with persistence_state.domain_transaction() as records:
        assert records.read(RecordKind.SOURCE_ITEM, item.record_id) is None
    assert persistence_state.audit_entries() == ()


def test_caught_failure_contract(
    persistence_state: EncryptedStateStore | PostgresStateStore, clock: FakeClock
) -> None:
    first, second = source(clock), source(clock)
    ctx = context(clock, "contract", "savepoints")
    with persistence_state.domain_transaction() as records:
        records.create(first, ctx)
        with pytest.raises((psycopg.IntegrityError, sqlcipher.IntegrityError)):
            records.create(first, ctx)
        records.create(second, ctx)
    assert persistence_state.verify_audit_chain().entries_verified == 2


def test_invalid_transition_preserves_record_and_audit(
    persistence_state: EncryptedStateStore | PostgresStateStore, clock: FakeClock
) -> None:
    item = source(clock)
    ctx = context(clock, "contract", "invalid-transition")
    with persistence_state.domain_transaction() as records:
        records.create(item, ctx)
        with pytest.raises(ValidationError):
            records.transition(RecordKind.SOURCE_ITEM, item.record_id, "", ctx)
        assert records.read(RecordKind.SOURCE_ITEM, item.record_id) == item
    assert persistence_state.verify_audit_chain().entries_verified == 1


def test_deleted_identity_cannot_be_resurrected(
    persistence_state: EncryptedStateStore | PostgresStateStore, clock: FakeClock
) -> None:
    item = source(clock)
    ctx = context(clock, "contract", "deleted")
    with persistence_state.domain_transaction() as records:
        records.create(item, ctx)
        records.delete(RecordKind.SOURCE_ITEM, item.record_id, ctx)
    with persistence_state.domain_transaction() as records:
        with pytest.raises(ValueError, match="deleted record identity"):
            records.create(item, ctx)
        assert records.read(RecordKind.SOURCE_ITEM, item.record_id) is None
    assert persistence_state.verify_audit_chain().entries_verified == 2
