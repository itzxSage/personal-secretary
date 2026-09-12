"""Transactional replay claims and encrypted durable jobs; no provider execution."""

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Final, Literal, final
from uuid import UUID, uuid4

import psycopg
from pydantic import AwareDatetime

from secretary_service.authority import Approval
from secretary_service.cloud_crypto import CloudCipher
from secretary_service.models import Execution, FrozenModel, RecordId, RecordKind, TransitionContext

if TYPE_CHECKING:
    from secretary_service.authority_consumption import ConsumptionKey
    from secretary_service.persistence import DomainRecords
    from secretary_service.postgres_types import PgConnection

CLAIM_DURATION: Final = timedelta(minutes=2)

type JobStatus = Literal["queued", "running", "uncertain", "succeeded", "cancelled", "expired"]


class PendingCalendarOperation(FrozenModel):
    """Immutable authorization and payload binding retained for worker revalidation."""

    operation_id: UUID
    proposal_id: RecordId
    payload: str
    approval: Approval
    authorized_until: AwareDatetime
    status: JobStatus = "queued"
    claim_token: UUID | None = None
    claim_deadline: AwareDatetime | None = None
    result_reference: str | None = None


@final
class PostgresConsumptionStore:
    """Consume all replay keys under the caller's transaction, never committing it."""

    def __init__(self, connection: "PgConnection", require_active: Callable[[], None]) -> None:
        """Bind replay storage to the active execution unit of work."""
        self._connection = connection
        self._require_active = require_active

    def consume(self, keys: tuple["ConsumptionKey", ...]) -> bool:
        """A conflicting key rolls back every key in this claim, but not the caller."""
        self._require_active()
        try:
            with self._connection.transaction():
                for namespace, identifier in keys:
                    _ = self._connection.execute(
                        "INSERT INTO lifeos_consumption VALUES (%s, %s)", (namespace, identifier)
                    )
        except psycopg.errors.UniqueViolation:
            return False
        return True


@final
class PostgresOutbox:
    """Persist job state with audit under the cell lock.

    A running job whose process disappears becomes uncertain. It can never be
    claimed again automatically, since its provider effect may already exist.
    """

    def __init__(
        self,
        connection: "PgConnection",
        cipher: CloudCipher,
        records: "DomainRecords",
        require_active: Callable[[], None],
    ) -> None:
        """Use only an active transaction supplied by the state store."""
        self._connection = connection
        self._cipher = cipher
        self._records = records
        self._require_active = require_active

    def read(self, operation_id: UUID) -> PendingCalendarOperation | None:
        """Authenticate the job's identity and persisted selection state."""
        self._require_active()
        row = self._connection.execute(
            "SELECT status, envelope FROM lifeos_outbox WHERE operation_id=%s", (operation_id,)
        ).fetchone()
        if row is None:
            return None
        job = PendingCalendarOperation.model_validate_json(
            self._cipher.open(row[1], ("outbox", str(operation_id), row[0]))
        )
        if job.operation_id != operation_id or job.status != row[0]:
            message = "outbox identity mismatch"
            raise ValueError(message)
        return job

    def enqueue(self, job: PendingCalendarOperation, context: TransitionContext) -> None:
        """Persist a validated pending job and its execution audit atomically.

        Internal persistence primitive, not an authorization decision. The public
        queue service verifies the signed approval and consumes it in this unit.
        """
        self._require_active()
        if job.status != "queued" or job.claim_token is not None:
            message = "new jobs must be unclaimed"
            raise ValueError(message)
        with self._connection.transaction():
            _ = self._connection.execute(
                "INSERT INTO lifeos_outbox VALUES (%s, %s, %s)",
                (job.operation_id, job.status, self._seal(job)),
            )
            self._records.create(
                Execution(
                    record_id=RecordId(job.operation_id),
                    proposal_id=job.proposal_id,
                    created_at=context.occurred_at,
                    state="queued",
                    outcome="awaiting-provider",
                ),
                context,
            )

    def _seal(self, job: PendingCalendarOperation) -> str:
        return self._cipher.seal(
            job.model_dump_json(), ("outbox", str(job.operation_id), job.status)
        )

    def _save(self, job: PendingCalendarOperation, context: TransitionContext) -> None:
        with self._connection.transaction():
            _ = self._connection.execute(
                "UPDATE lifeos_outbox SET status=%s, envelope=%s WHERE operation_id=%s",
                (job.status, self._seal(job), job.operation_id),
            )
            self._records.transition(
                RecordKind.EXECUTION, RecordId(job.operation_id), job.status, context
            )

    def claim(
        self, context: TransitionContext, duration: timedelta = CLAIM_DURATION
    ) -> PendingCalendarOperation | None:
        """Reserve one queued job; authorization is checked again by the future worker."""
        self._require_active()
        if duration <= timedelta(0):
            message = "claim duration must be positive"
            raise ValueError(message)
        now = context.occurred_at
        _require_aware(now)
        rows = self._connection.execute(
            """SELECT operation_id::text FROM lifeos_outbox
            WHERE status='queued' ORDER BY operation_id"""
        ).fetchall()
        for row in rows:
            job = self.read(UUID(row[0]))
            if job is None:
                continue
            if job.authorized_until <= now:
                self._save(job.model_copy(update={"status": "expired"}), context)
                continue
            claimed = job.model_copy(
                update={
                    "status": "running",
                    "claim_token": uuid4(),
                    "claim_deadline": min(now + duration, job.authorized_until),
                }
            )
            self._save(claimed, context)
            return claimed
        return None

    def complete(
        self,
        operation_id: UUID,
        claim_token: UUID,
        result_reference: str,
        context: TransitionContext,
    ) -> None:
        """Accept a result only from the current unexpired claim."""
        job = self.read(operation_id)
        _require_aware(context.occurred_at)
        if (
            job is None
            or job.status != "running"
            or job.claim_token != claim_token
            or job.claim_deadline is None
            or job.claim_deadline <= context.occurred_at
        ):
            message = "stale or invalid job claim"
            raise ValueError(message)
        self._save(
            job.model_copy(update={"status": "succeeded", "result_reference": result_reference}),
            context,
        )

    def cancel_claim(
        self, operation_id: UUID, claim_token: UUID, context: TransitionContext
    ) -> None:
        """Cancel before any provider call when trusted revalidation rejects the job."""
        job = self.read(operation_id)
        if job is None or job.status != "running" or job.claim_token != claim_token:
            message = "invalid cancellation claim"
            raise ValueError(message)
        self._save(job.model_copy(update={"status": "cancelled"}), context)

    def recover(self, context: TransitionContext) -> int:
        """Mark timed-out running work uncertain; never put it back in the queue."""
        self._require_active()
        _require_aware(context.occurred_at)
        rows = self._connection.execute(
            "SELECT operation_id::text FROM lifeos_outbox WHERE status='running'"
        ).fetchall()
        recovered = 0
        for row in rows:
            job = self.read(UUID(row[0]))
            if (
                job is not None
                and job.claim_deadline is not None
                and job.claim_deadline <= context.occurred_at
            ):
                self._save(job.model_copy(update={"status": "uncertain"}), context)
                recovered += 1
        return recovered

    def reconcile(
        self,
        operation_id: UUID,
        status: Literal["succeeded", "cancelled"],
        result_reference: str,
        context: TransitionContext,
    ) -> None:
        """Record a trusted reconciler's provider evidence; never re-execute the job."""
        job = self.read(operation_id)
        if job is None or job.status != "uncertain":
            message = "only uncertain jobs can be reconciled"
            raise ValueError(message)
        self._save(
            job.model_copy(update={"status": status, "result_reference": result_reference}), context
        )


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        message = "job clock must be timezone aware"
        raise ValueError(message)
