"""Governed learning, procedural-skill proposals, and observability."""

from secretary_service.learning.audit import LearningAuditor
from secretary_service.learning.errors import (
    CorrectionMismatchError,
    InvalidProposalTransitionError,
    ProposalNotFoundError,
    StaleProposalStatusError,
)
from secretary_service.learning.learner import learn_proposals
from secretary_service.learning.lifecycle import LearningLifecycle
from secretary_service.learning.models import (
    ActualOutcome,
    LearningMetrics,
    LearningObservation,
    LearningProposal,
    LearningProvenance,
    LearningSource,
    MemoryUpdateProposal,
    ObservationTrigger,
    PlannedActivity,
    ProposalCorrection,
    ProposalDeletion,
    ProposalKind,
    ProposalStatus,
    SkillProposal,
)
from secretary_service.learning.repository import LearningRepository

__all__ = [
    "ActualOutcome",
    "CorrectionMismatchError",
    "InvalidProposalTransitionError",
    "LearningAuditor",
    "LearningLifecycle",
    "LearningMetrics",
    "LearningObservation",
    "LearningProposal",
    "LearningProvenance",
    "LearningRepository",
    "LearningSource",
    "MemoryUpdateProposal",
    "ObservationTrigger",
    "PlannedActivity",
    "ProposalCorrection",
    "ProposalDeletion",
    "ProposalKind",
    "ProposalNotFoundError",
    "ProposalStatus",
    "SkillProposal",
    "StaleProposalStatusError",
    "learn_proposals",
]
