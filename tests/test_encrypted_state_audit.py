from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlcipher3 import dbapi2 as sqlcipher

from secretary_service.audit import AuditTamperError
from secretary_service.keys import DeterministicTestKeyProvider
from secretary_service.models import (
    ActorId,
    Approval,
    CorrelationId,
    Execution,
    Proposal,
    RecordId,
    RecordKind,
    SourceItem,
    TransitionContext,
)
from secretary_service.storage import EncryptedStateStore, StoreKeyError
from tests.helpers import FakeClock


def context(clock: FakeClock, actor: str, correlation_id: str) -> TransitionContext:
    return TransitionContext(
        actor=ActorId(actor),
        correlation_id=CorrelationId(correlation_id),
        occurred_at=clock.now(),
    )


def test_intake_proposal_approval_execution_produces_ordered_verified_audit(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    correlation = "corr-happy-path"
    source_id = RecordId(UUID("00000000-0000-0000-0000-000000000001"))
    proposal_id = RecordId(UUID("00000000-0000-0000-0000-000000000002"))
    store.create(
        SourceItem(
            record_id=source_id,
            created_at=clock.now(),
            source_type="communication",
            external_id="fixture-1",
            raw_content="private fixture body",
        ),
        context(clock, "intake", correlation),
    )
    store.create(
        Proposal(
            record_id=proposal_id,
            created_at=clock.now(),
            proposal_type="draft",
            payload="redacted proposal fixture",
            state="proposed",
            policy_decision="unassessed",
            provider_version="fake-v1",
            reversible=True,
            approval_state="pending",
        ),
        context(clock, "provider:fake", correlation),
    )
    store.create(
        Approval(
            record_id=RecordId(UUID("00000000-0000-0000-0000-000000000003")),
            created_at=clock.now(),
            proposal_id=proposal_id,
            decision="approved",
            state="recorded",
        ),
        context(clock, "user", correlation),
    )
    store.create(
        Execution(
            record_id=RecordId(UUID("00000000-0000-0000-0000-000000000004")),
            created_at=clock.now(),
            proposal_id=proposal_id,
            outcome="dry-run-recorded",
            state="recorded",
        ),
        context(clock, "dry-run", correlation),
    )

    entries = store.audit_entries()
    assert [entry.sequence for entry in entries] == [1, 2, 3, 4]
    assert {entry.correlation_id for entry in entries} == {CorrelationId(correlation)}
    assert all(entry.actor for entry in entries)
    assert store.verify_audit_chain().entries_verified == 4


def test_audit_tamper_is_rejected_on_next_trusted_operation(
    store: EncryptedStateStore,
    keys: DeterministicTestKeyProvider,
    clock: FakeClock,
) -> None:
    store.create(
        SourceItem(
            record_id=RecordId(UUID("00000000-0000-0000-0000-000000000010")),
            created_at=clock.now(),
            source_type="communication",
            external_id="fixture-tamper",
            raw_content="tamper target",
        ),
        context(clock, "intake", "corr-tamper"),
    )
    connection = sqlcipher.connect(str(store.path))
    _ = connection.execute(f"PRAGMA key = \"x'{keys.database_key().hex()}'\"")
    _ = connection.execute("DROP TRIGGER audit_entries_no_update")
    _ = connection.execute("UPDATE audit_entries SET action_class='forged' WHERE sequence=1")
    connection.commit()
    connection.close()

    with pytest.raises(AuditTamperError):
        _ = store.verify_audit_chain()
    with pytest.raises(AuditTamperError):
        store.create(
            SourceItem(
                record_id=RecordId(UUID("00000000-0000-0000-0000-000000000011")),
                created_at=clock.now(),
                source_type="communication",
                external_id="blocked-after-tamper",
                raw_content="must not persist",
            ),
            context(clock, "intake", "corr-blocked"),
        )


def test_sensitive_values_are_not_plaintext_and_wrong_key_fails_closed(
    tmp_path: Path,
    clock: FakeClock,
    keys: DeterministicTestKeyProvider,
) -> None:
    database_path = tmp_path / "encrypted.sqlite"
    raw_fixture = "unique-sensitive-fixture-7adab0"
    with EncryptedStateStore.open(database_path, keys, clock) as state:
        state.create(
            SourceItem(
                record_id=RecordId(UUID("00000000-0000-0000-0000-000000000020")),
                created_at=clock.now(),
                source_type="communication",
                external_id="fixture-encrypted",
                raw_content=raw_fixture,
            ),
            context(clock, "intake", "corr-encrypted"),
        )

    assert raw_fixture.encode() not in database_path.read_bytes()
    wrong_keys = DeterministicTestKeyProvider.from_seed(b"wrong-key")
    with pytest.raises(StoreKeyError):
        _ = EncryptedStateStore.open(database_path, wrong_keys, clock)


def test_domain_transition_is_append_only_and_requires_trace_context(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    proposal_id = RecordId(UUID("00000000-0000-0000-0000-000000000030"))
    store.create(
        Proposal(
            record_id=proposal_id,
            created_at=clock.now(),
            proposal_type="draft",
            payload="fixture",
            state="proposed",
            policy_decision="unassessed",
            provider_version="fake-v1",
            reversible=True,
            approval_state="pending",
        ),
        context(clock, "provider:fake", "corr-transition"),
    )
    store.transition(
        RecordKind.PROPOSAL,
        proposal_id,
        "reviewed",
        context(clock, "user", "corr-transition"),
    )

    assert store.version_count(RecordKind.PROPOSAL, proposal_id) == 2
    current = store.read(RecordKind.PROPOSAL, proposal_id)
    assert current is not None
    assert current.state == "reviewed"

    with pytest.raises(ValidationError, match="at least 1 character"):
        _ = TransitionContext(
            actor=ActorId(""),
            correlation_id=CorrelationId(""),
            occurred_at=clock.now(),
        )
