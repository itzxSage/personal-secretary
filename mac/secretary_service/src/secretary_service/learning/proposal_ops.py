"""Pure proposal-state operations for the governed learning lifecycle."""

from datetime import datetime

from secretary_service.learning.errors import (
    CorrectionMismatchError,
    InvalidProposalTransitionError,
    StaleProposalStatusError,
)
from secretary_service.learning.models import (
    LearningProposal,
    MemoryUpdateProposal,
    ProposalCorrection,
    ProposalKind,
    ProposalStatus,
    SkillProposal,
)


def require_status(
    current: LearningProposal,
    expected: ProposalStatus,
    requested: ProposalStatus,
) -> None:
    """Block a lifecycle transition that is not allowed from the current status."""
    if current.status is not expected:
        raise InvalidProposalTransitionError(
            proposal_id=current.proposal_id,
            current=current.status,
            requested=requested,
        )


def require_expected_status(
    current: LearningProposal,
    expected: ProposalStatus,
) -> None:
    """Block a correction or deletion targeting an obsolete status."""
    if expected is not current.status:
        raise StaleProposalStatusError(
            proposal_id=current.proposal_id,
            expected=expected,
            current=current.status,
        )


def apply_correction(
    current: LearningProposal,
    correction: ProposalCorrection,
    occurred_at: datetime,
) -> LearningProposal:
    """Apply a kind-matched correction while retaining proposal identity."""
    match current:  # noqa: MATCH_OK
        case SkillProposal():
            if correction.procedure is None:
                raise CorrectionMismatchError(
                    proposal_id=current.proposal_id, kind=ProposalKind.PROCEDURE
                )
            return current.model_copy(
                update={
                    "procedure": correction.procedure,
                    "status": ProposalStatus.CORRECTED,
                    "corrected_at": occurred_at,
                    "correction_note": correction.note,
                }
            )
        case MemoryUpdateProposal():
            if correction.content is None:
                raise CorrectionMismatchError(
                    proposal_id=current.proposal_id, kind=ProposalKind.MEMORY_UPDATE
                )
            return current.model_copy(
                update={
                    "content": correction.content,
                    "status": ProposalStatus.CORRECTED,
                    "corrected_at": occurred_at,
                    "correction_note": correction.note,
                }
            )
