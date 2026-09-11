"""Domain errors for the governed learning proposal lifecycle."""

from typing import final, override

from secretary_service.learning.models import ProposalKind, ProposalStatus
from secretary_service.models import RecordId


@final
class ProposalNotFoundError(Exception):
    """A requested live learning proposal does not exist."""

    def __init__(self, proposal_id: RecordId) -> None:
        """Initialize the missing-proposal error."""
        super().__init__(proposal_id)
        self.proposal_id = proposal_id


@final
class InvalidProposalTransitionError(Exception):
    """A proposal lifecycle transition is not allowed from its current status."""

    def __init__(
        self,
        proposal_id: RecordId,
        current: ProposalStatus,
        requested: ProposalStatus,
    ) -> None:
        """Initialize the invalid transition details."""
        super().__init__(proposal_id, current, requested)
        self.proposal_id = proposal_id
        self.current = current
        self.requested = requested

    @override
    def __str__(self) -> str:
        return (
            f"proposal {self.proposal_id} cannot move from {self.current.value} "
            f"to {self.requested.value}"
        )


@final
class StaleProposalStatusError(Exception):
    """A correction or deletion targets an obsolete proposal status."""

    def __init__(
        self,
        proposal_id: RecordId,
        expected: ProposalStatus,
        current: ProposalStatus,
    ) -> None:
        """Initialize the stale status details."""
        super().__init__(proposal_id, expected, current)
        self.proposal_id = proposal_id
        self.expected = expected
        self.current = current

    @override
    def __str__(self) -> str:
        return (
            f"proposal {self.proposal_id} expected status {self.expected.value}, "
            f"current is {self.current.value}"
        )


@final
class CorrectionMismatchError(Exception):
    """A correction payload does not match the proposal kind."""

    def __init__(self, proposal_id: RecordId, kind: ProposalKind) -> None:
        """Initialize the mismatch details."""
        super().__init__(proposal_id, kind)
        self.proposal_id = proposal_id
        self.kind = kind

    @override
    def __str__(self) -> str:
        return f"correction payload does not match proposal kind {self.kind.value}"
