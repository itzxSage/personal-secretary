"""Backend-independent domain operations exposed within a unit of work."""

from contextlib import AbstractContextManager
from typing import Protocol

from secretary_service.models import DomainRecord, RecordId, RecordKind, TransitionContext


class DomainRecords(Protocol):
    """Domain mutations whose durability belongs to the enclosing unit of work."""

    def create(self, record: DomainRecord, context: TransitionContext) -> None:
        """Create a record and its audit entry."""
        ...

    def read(self, kind: RecordKind, record_id: RecordId) -> DomainRecord | None:
        """Read the latest live version."""
        ...

    def transition(
        self, kind: RecordKind, record_id: RecordId, new_state: str, context: TransitionContext
    ) -> None:
        """Append a state version and its audit entry."""
        ...

    def delete(self, kind: RecordKind, record_id: RecordId, context: TransitionContext) -> None:
        """Purge content and retain deletion evidence and audit."""
        ...


class DomainUnitOfWork(Protocol):
    """Commit successful domain batches; roll back the entire batch on failure.

    Returned repositories are used only inside their context. This first seam
    covers domain records and their audit entries, not provider calls, authority
    consumption, conversations, or jobs. Those require further migration.
    """

    def domain_transaction(self) -> AbstractContextManager[DomainRecords]:
        """Acquire the transaction before reading versions or mutating records."""
        ...
