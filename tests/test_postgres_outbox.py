import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from secretary_service.authority import Approval, PolicyViolationError, ProposalRecord
from secretary_service.durable_calendar import approve_and_enqueue_calendar, claim_verified_calendar
from secretary_service.enrollment import DeviceRegistry
from secretary_service.fixture_authority import FIXTURE_PUBLIC_KEY
from secretary_service.models import ActorId, Proposal, RecordKind
from secretary_service.postgres_outbox import PostgresOutbox
from tests.helpers import FakeClock
from tests.postgres_support import PostgresCase
from tests.test_authority import DEFAULT_DEVICE_ID, make_approval, make_proposal
from tests.test_encrypted_state_audit import context


def prepare(case: PostgresCase, clock: FakeClock) -> tuple[ProposalRecord, Approval]:
    proposal = make_proposal(clock, "calendar.apply", '{"events":[]}')
    with case.open() as store, store.domain_transaction() as records:
        records.create(
            Proposal(
                record_id=proposal.proposal_id,
                created_at=proposal.created_at,
                state="proposed",
                proposal_type="calendar.apply",
                payload=proposal.payload,
                policy_decision="pending",
                provider_version="fixture-v1",
                reversible=True,
                approval_state="pending",
            ),
            context(clock, "test", "proposal"),
        )
    with case.open() as store, store.execution_transaction() as unit:
        devices = DeviceRegistry(clock, unit.devices)
        _ = devices.enroll(
            DEFAULT_DEVICE_ID, "a" * 64, ActorId("owner"), approval_public_key=FIXTURE_PUBLIC_KEY
        )
    return proposal, make_approval(clock, proposal)


def test_atomic_approval_and_enqueue(pg_case: PostgresCase, clock: FakeClock) -> None:
    proposal, approval = prepare(pg_case, clock)
    with pg_case.open() as store:
        job = approve_and_enqueue_calendar(store, approval, clock)
    with pg_case.open() as store, store.execution_transaction() as unit:
        current = unit.records.read(RecordKind.PROPOSAL, proposal.proposal_id)
        assert current is not None
        assert current.state == "approved"
        assert unit.outbox.read(job.operation_id) == job
        assert not unit.consumption.consume((("approve", str(approval.fact_id)),))
        assert store.verify_audit_chain().entries_verified == 4


def test_enqueue_failure_rolls_back_approval_and_all_replay_markers(
    pg_case: PostgresCase, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    proposal, approval = prepare(pg_case, clock)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError

    with pg_case.open() as store:
        with monkeypatch.context() as patch:
            patch.setattr(PostgresOutbox, "enqueue", fail)
            with pytest.raises(RuntimeError):
                _ = approve_and_enqueue_calendar(store, approval, clock)
        with store.domain_transaction() as records:
            current = records.read(RecordKind.PROPOSAL, proposal.proposal_id)
            assert current is not None
            assert current.state == "proposed"
        assert store.verify_audit_chain().entries_verified == 2
        # Identical signed approval remains usable after the rolled-back attempt.
        _ = approve_and_enqueue_calendar(store, approval, clock)
    with psycopg.connect(pg_case.dsn) as connection:
        assert connection.execute("SELECT count(*) FROM lifeos_consumption").fetchone() == (3,)
        assert connection.execute("SELECT count(*) FROM lifeos_outbox").fetchone() == (1,)


def test_concurrent_approval_replay_creates_one_job(
    pg_case: PostgresCase, clock: FakeClock
) -> None:
    _, approval = prepare(pg_case, clock)
    barrier = Barrier(3)

    def approve(_index: int) -> bool:
        with pg_case.open() as store:
            _ = barrier.wait(timeout=10)
            try:
                _ = approve_and_enqueue_calendar(store, approval, clock)
            except PolicyViolationError:
                return False
            return True

    with ThreadPoolExecutor(max_workers=3) as executor:
        assert sorted(executor.map(approve, range(3))) == [False, False, True]
    with psycopg.connect(pg_case.dsn) as connection:
        assert connection.execute("SELECT count(*) FROM lifeos_outbox").fetchone() == (1,)
    with pg_case.open() as store:
        assert store.verify_audit_chain().entries_verified == 4


def test_bad_or_revoked_approval_never_enqueues(pg_case: PostgresCase, clock: FakeClock) -> None:
    _, approval = prepare(pg_case, clock)
    with pg_case.open() as store:
        bad = approval.model_copy(update={"payload_hash": "f" * 64})
        with pytest.raises(PolicyViolationError):
            _ = approve_and_enqueue_calendar(store, bad, clock)
        with store.execution_transaction() as unit:
            _ = DeviceRegistry(clock, unit.devices).revoke(DEFAULT_DEVICE_ID, ActorId("owner"))
        with pytest.raises(PolicyViolationError):
            _ = approve_and_enqueue_calendar(store, approval, clock)
        assert store.verify_audit_chain().entries_verified == 3
    with psycopg.connect(pg_case.dsn) as connection:
        assert connection.execute("SELECT count(*) FROM lifeos_consumption").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM lifeos_outbox").fetchone() == (0,)


def test_worker_loss_requires_reconciliation_not_reexecution(
    pg_case: PostgresCase, clock: FakeClock
) -> None:
    _, approval = prepare(pg_case, clock)
    with pg_case.open() as store:
        job = approve_and_enqueue_calendar(store, approval, clock)
        with store.execution_transaction() as unit:
            claimed = unit.outbox.claim(context(clock, "worker", "claim"))
            assert claimed is not None
            assert claimed.claim_token is not None
    # Connection/process ends after durable claim; provider outcome is unknown.
    clock.advance(timedelta(minutes=3))
    with pg_case.open() as store, store.execution_transaction() as unit:
        assert unit.outbox.recover(context(clock, "recovery", "recover")) == 1
        assert unit.outbox.claim(context(clock, "worker", "retry")) is None
        uncertain = unit.outbox.read(job.operation_id)
        assert uncertain is not None
        assert uncertain.status == "uncertain"
        with pytest.raises(ValueError, match="stale"):
            unit.outbox.complete(
                job.operation_id,
                claimed.claim_token,
                "late-result",
                context(clock, "worker", "late"),
            )
        unit.outbox.reconcile(
            job.operation_id,
            "succeeded",
            "verified-provider-event",
            context(clock, "reconciler", "reconcile"),
        )
    with pg_case.open() as store, store.execution_transaction() as unit:
        completed = unit.outbox.read(job.operation_id)
        assert completed is not None
        assert completed.status == "succeeded"
        assert completed.result_reference == "verified-provider-event"
        assert unit.outbox.claim(context(clock, "worker", "duplicate")) is None


def test_only_one_concurrent_worker_claims_job(pg_case: PostgresCase, clock: FakeClock) -> None:
    _, approval = prepare(pg_case, clock)
    with pg_case.open() as store:
        _ = approve_and_enqueue_calendar(store, approval, clock)
    barrier = Barrier(2)

    def claim(_index: int) -> bool:
        with pg_case.open() as store:
            _ = barrier.wait(timeout=10)
            with store.execution_transaction() as unit:
                return unit.outbox.claim(context(clock, "worker", "claim")) is not None

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(claim, range(2))) == [False, True]


def test_expired_job_never_claimed(pg_case: PostgresCase, clock: FakeClock) -> None:
    _, approval = prepare(pg_case, clock)
    with pg_case.open() as store:
        job = approve_and_enqueue_calendar(store, approval, clock)
        clock.advance(timedelta(minutes=6))
        with store.execution_transaction() as unit:
            assert unit.outbox.claim(context(clock, "worker", "expired")) is None
            expired = unit.outbox.read(job.operation_id)
            assert expired is not None
            assert expired.status == "expired"


def test_result_requires_matching_claim_and_handles_expire(
    pg_case: PostgresCase, clock: FakeClock
) -> None:
    _, approval = prepare(pg_case, clock)
    with pg_case.open() as store:
        job = approve_and_enqueue_calendar(store, approval, clock)
        with store.execution_transaction() as unit:
            claimed = unit.outbox.claim(context(clock, "worker", "claim"))
            assert claimed is not None
            assert claimed.claim_token is not None
            with pytest.raises(ValueError, match="stale"):
                unit.outbox.complete(
                    job.operation_id, uuid4(), "forged", context(clock, "worker", "result")
                )
            unit.outbox.complete(
                job.operation_id,
                claimed.claim_token,
                "verified-event",
                context(clock, "worker", "result"),
            )
        with store.execution_transaction(), pytest.raises(RuntimeError, match="outside"):
            _ = unit.consumption.consume((("escaped", "bad"),))


def test_revocation_after_enqueue_blocks_dispatch(pg_case: PostgresCase, clock: FakeClock) -> None:
    _, approval = prepare(pg_case, clock)
    with pg_case.open() as store:
        job = approve_and_enqueue_calendar(store, approval, clock)
    with pg_case.open() as revoker, revoker.execution_transaction() as unit:
        _ = DeviceRegistry(clock, unit.devices).revoke(DEFAULT_DEVICE_ID, ActorId("owner"))
    with pg_case.open() as worker:
        assert claim_verified_calendar(worker, clock) is None
        with worker.execution_transaction() as unit:
            cancelled = unit.outbox.read(job.operation_id)
            assert cancelled is not None
            assert cancelled.status == "cancelled"


@pytest.mark.parametrize("mode", ["uncommitted", "claimed"])
def test_actual_process_exit_preserves_transaction_and_claim_rules(
    pg_case: PostgresCase, clock: FakeClock, mode: str
) -> None:
    _, approval = prepare(pg_case, clock)
    with pg_case.open() as store:
        job = approve_and_enqueue_calendar(store, approval, clock)
        before = store.verify_audit_chain().entries_verified
    environment = dict(os.environ)
    environment.update(
        {
            "LIFEOS_CRASH_DSN": pg_case.dsn,
            "LIFEOS_CRASH_TENANT": str(pg_case.tenant_id),
            "LIFEOS_CRASH_DATA_KEY": pg_case.keys.data.get_secret_value().hex(),
            "LIFEOS_CRASH_AUDIT_KEY": pg_case.keys.audit.get_secret_value().hex(),
            "LIFEOS_CRASH_NOW": clock.now().isoformat(),
            "LIFEOS_CRASH_MODE": mode,
        }
    )
    child = subprocess.run(
        [sys.executable, "-m", "tests.postgres_crash_worker"],
        env=environment,
        check=False,
        capture_output=True,
        timeout=20,
    )
    assert child.returncode == 23
    with pg_case.open() as store, store.execution_transaction() as unit:
        if mode == "uncommitted":
            assert store.verify_audit_chain().entries_verified == before
            assert unit.consumption.consume((("crash", "uncommitted"),))
        else:
            assert unit.outbox.claim(context(clock, "worker", "duplicate")) is None
            clock.advance(timedelta(minutes=3))
            assert unit.outbox.recover(context(clock, "recovery", "crash")) == 1
            recovered = unit.outbox.read(job.operation_id)
            assert recovered is not None
            assert recovered.status == "uncertain"
