from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

from secretary_service.keys import DeterministicTestKeyProvider
from secretary_service.ledger import AuditLedger
from secretary_service.models import RecordId, RecordKind, SourceItem
from secretary_service.storage import EncryptedStateStore
from tests.helpers import FakeClock
from tests.test_encrypted_state_audit import context

if TYPE_CHECKING:
    from secretary_service.persistence import DomainUnitOfWork


def source(clock: FakeClock) -> SourceItem:
    return SourceItem(
        record_id=RecordId(uuid4()),
        created_at=clock.now(),
        source_type="communication",
        external_id="transaction-fixture",
        raw_content="synthetic content",
    )


def test_batch_commits_records_and_audit(store: EncryptedStateStore, clock: FakeClock) -> None:
    unit: DomainUnitOfWork = store
    first, second = source(clock), source(clock)
    with unit.domain_transaction() as records:
        records.create(first, context(clock, "test", "batch"))
        records.create(second, context(clock, "test", "batch"))
        assert records.read(RecordKind.SOURCE_ITEM, first.record_id) == first
    assert store.read(RecordKind.SOURCE_ITEM, second.record_id) == second
    assert store.verify_audit_chain().entries_verified == 2


def test_batch_failure_rolls_back_successful_operations(
    store: EncryptedStateStore, clock: FakeClock
) -> None:
    first = source(clock)
    with pytest.raises(sqlcipher.IntegrityError), store.domain_transaction() as records:  # noqa: PT012 -- exercises rollback of a multi-operation batch
        records.create(first, context(clock, "test", "batch"))
        records.create(first, context(clock, "test", "duplicate"))
    assert store.records() == ()
    assert store.audit_entries() == ()
    store.create(first, context(clock, "test", "retry"))
    assert store.verify_audit_chain().entries_verified == 1


def test_caught_nested_failure_preserves_outer_work(
    store: EncryptedStateStore, clock: FakeClock
) -> None:
    first, second = source(clock), source(clock)
    with store.domain_transaction() as records:
        records.create(first, context(clock, "test", "batch"))
        with pytest.raises(sqlcipher.IntegrityError):
            records.create(first, context(clock, "test", "duplicate"))
        records.create(second, context(clock, "test", "batch"))
    assert len(store.records()) == 2
    assert store.verify_audit_chain().entries_verified == 2


@pytest.mark.parametrize("operation", ["create", "transition", "delete"])
def test_non_database_audit_failure_leaves_no_pending_mutation(
    store: EncryptedStateStore,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    item = source(clock)
    if operation != "create":
        store.create(item, context(clock, "test", "original"))
    before = store.records()
    audit_before = store.audit_entries()

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError

    with monkeypatch.context() as patch:
        patch.setattr(AuditLedger, "append", fail)
        with pytest.raises(RuntimeError):  # noqa: PT012 -- inject the same failure into each mutation path
            if operation == "create":
                store.create(item, context(clock, "test", "failed"))
            elif operation == "transition":
                store.transition(
                    RecordKind.SOURCE_ITEM,
                    item.record_id,
                    "processed",
                    context(clock, "test", "failed"),
                )
            else:
                store.delete_record(
                    RecordKind.SOURCE_ITEM, item.record_id, context(clock, "test", "failed")
                )
    assert store.records() == before
    assert store.audit_entries() == audit_before
    # A later successful commit must not accidentally persist the failed write.
    store.create(source(clock), context(clock, "test", "later"))
    assert store.verify_audit_chain().entries_verified == len(audit_before) + 1
    assert store.read(RecordKind.SOURCE_ITEM, item.record_id) == (
        None if operation == "create" else item
    )


def test_cancellation_rolls_back_batch(store: EncryptedStateStore, clock: FakeClock) -> None:
    with pytest.raises(KeyboardInterrupt), store.domain_transaction() as records:  # noqa: PT012 -- cancellation after an actual write
        records.create(source(clock), context(clock, "test", "cancel"))
        raise KeyboardInterrupt
    assert store.records() == ()
    assert store.audit_entries() == ()


def test_batch_is_invisible_until_outer_commit(
    store: EncryptedStateStore,
    clock: FakeClock,
    keys: DeterministicTestKeyProvider,
) -> None:
    first, second = source(clock), source(clock)
    with EncryptedStateStore.open(store.path, keys, clock) as observer:
        with store.domain_transaction() as records:
            records.create(first, context(clock, "test", "batch"))
            records.create(second, context(clock, "test", "batch"))
            assert observer.records() == ()
            assert observer.audit_entries() == ()
        assert len(observer.records()) == 2
        assert observer.verify_audit_chain().entries_verified == 2
    with EncryptedStateStore.open(store.path, keys, clock) as reopened:
        assert reopened.read(RecordKind.SOURCE_ITEM, first.record_id) == first
        assert reopened.verify_audit_chain().entries_verified == 2
