"""SQLCipher persistence for governed learning records."""

from typing import final

from sqlcipher3 import dbapi2 as sqlcipher

from secretary_service.learning.audit import LearningAuditor
from secretary_service.learning.errors import ProposalNotFoundError
from secretary_service.learning.learner import learn_proposals
from secretary_service.learning.lifecycle import LearningLifecycle
from secretary_service.learning.models import (
    LEARNING_PROPOSAL_ADAPTER,
    LearningMetrics,
    LearningObservation,
    LearningProposal,
    ProposalCorrection,
    ProposalDeletion,
    ProposalStatus,
    SkillProposal,
)
from secretary_service.learning.proposal_ops import (
    apply_correction,
    require_expected_status,
    require_status,
)
from secretary_service.models import RecordId, TransitionContext


def _pattern(proposal: LearningProposal) -> str:
    """Return the stable pattern identity used for deletion suppression."""
    if isinstance(proposal, SkillProposal):
        return proposal.trigger.model_dump_json()
    return proposal.content


@final
class LearningRepository:
    """Persist learning observations and the governed proposal lifecycle."""

    def __init__(
        self,
        connection: sqlcipher.Connection,
        auditor: LearningAuditor,
        lifecycle: LearningLifecycle,
    ) -> None:
        """Bind encrypted persistence, audit, and retention services."""
        self._connection = connection
        self._auditor = auditor
        self._lifecycle = lifecycle

    def record_observation(
        self,
        observation: LearningObservation,
        context: TransitionContext,
    ) -> None:
        """Persist a new planned-vs-actual observation and provenance audit."""
        self._auditor.verify()
        _ = self._connection.execute(
            "INSERT INTO learning_observations VALUES(?, ?, ?, ?)",
            (
                str(observation.observation_id),
                None if observation.retain_until is None else observation.retain_until.isoformat(),
                observation.model_dump_json(),
                observation.observed_at.isoformat(),
            ),
        )
        fingerprint = self._auditor.fingerprint(observation.model_dump_json())
        self._auditor.append(observation.observation_id, "observed", fingerprint, context)
        self._connection.commit()

    def observations(self) -> tuple[LearningObservation, ...]:
        """Return only current, unexpired learning observations."""
        query = (
            "SELECT content_json FROM learning_observations "
            "WHERE retain_until IS NULL OR retain_until>? "
            "ORDER BY observed_at, observation_id"
        )
        rows = self._connection.execute(query, (self._lifecycle.now().isoformat(),)).fetchall()
        return tuple(LearningObservation.model_validate_json(str(row[0])) for row in rows)

    def learn(self, context: TransitionContext) -> tuple[LearningProposal, ...]:
        """Propose explainable lessons from repeated outcomes without activating them."""
        self._auditor.verify()
        proposals = learn_proposals(self.observations(), self._lifecycle.now())
        created: list[LearningProposal] = []
        for proposal in proposals:
            if self._exists(proposal.proposal_id) or self._suppressed(proposal):
                continue
            self._insert(proposal)
            fingerprint = self._auditor.fingerprint(proposal.model_dump_json())
            self._auditor.append(proposal.proposal_id, "proposed", fingerprint, context)
            created.append(proposal)
        self._connection.commit()
        return tuple(created)

    def proposals(self) -> tuple[LearningProposal, ...]:
        """Return all live proposals in creation order."""
        rows = self._connection.execute(
            "SELECT content_json FROM learning_proposals ORDER BY created_at, proposal_id"
        ).fetchall()
        return tuple(LEARNING_PROPOSAL_ADAPTER.validate_json(str(row[0])) for row in rows)

    def route_candidates(self) -> tuple[LearningProposal, ...]:
        """Return only explicitly approved proposals eligible for routing."""
        query = (
            "SELECT content_json FROM learning_proposals WHERE status=? "
            "ORDER BY created_at, proposal_id"
        )
        rows = self._connection.execute(query, (ProposalStatus.APPROVED.value,)).fetchall()
        return tuple(LEARNING_PROPOSAL_ADAPTER.validate_json(str(row[0])) for row in rows)

    def approve(
        self,
        proposal_id: RecordId,
        context: TransitionContext,
    ) -> LearningProposal:
        """Explicitly approve a proposed lesson so it becomes routable."""
        self._auditor.verify()
        current = self._read(proposal_id)
        require_status(current, ProposalStatus.PROPOSED, ProposalStatus.APPROVED)
        approved = current.model_copy(update={"status": ProposalStatus.APPROVED})
        self._replace(current, approved, "approved", context)
        self._connection.commit()
        return approved

    def correct(
        self,
        proposal_id: RecordId,
        correction: ProposalCorrection,
        context: TransitionContext,
    ) -> LearningProposal:
        """Replace a proposal's procedure or content while retaining its identity."""
        self._auditor.verify()
        current = self._read(proposal_id)
        require_expected_status(current, correction.expected_status)
        corrected = apply_correction(current, correction, context.occurred_at)
        self._replace(current, corrected, "corrected", context)
        self._connection.commit()
        return corrected

    def delete(
        self,
        proposal_id: RecordId,
        deletion: ProposalDeletion,
        context: TransitionContext,
    ) -> None:
        """Erase a user-selected proposal and retain keyed fingerprints."""
        self._auditor.verify()
        current = self._read(proposal_id)
        require_expected_status(current, deletion.expected_status)
        fingerprint = self._auditor.fingerprint(current.model_dump_json())
        pattern_fingerprint = self._auditor.fingerprint(_pattern(current))
        with self._lifecycle.deletion():
            _ = self._connection.execute(
                "DELETE FROM learning_proposals WHERE proposal_id=?", (str(proposal_id),)
            )
        _ = self._connection.execute(
            "INSERT INTO learning_tombstones VALUES(?, ?, ?, ?, ?)",
            (
                str(proposal_id),
                context.occurred_at.isoformat(),
                "user_deletion",
                fingerprint,
                pattern_fingerprint,
            ),
        )
        self._auditor.append(proposal_id, "deleted", fingerprint, context)
        self._connection.commit()

    def purge_expired(self, context: TransitionContext) -> int:
        """Erase all retention-expired observations and retain keyed fingerprints."""
        self._auditor.verify()
        query = (
            "SELECT content_json FROM learning_observations "
            "WHERE retain_until IS NOT NULL AND retain_until<=?"
        )
        rows = self._connection.execute(query, (self._lifecycle.now().isoformat(),)).fetchall()
        expired = tuple(LearningObservation.model_validate_json(str(row[0])) for row in rows)
        for observation in expired:
            fingerprint = self._auditor.fingerprint(observation.model_dump_json())
            with self._lifecycle.deletion():
                _ = self._connection.execute(
                    "DELETE FROM learning_observations WHERE observation_id=?",
                    (str(observation.observation_id),),
                )
            self._auditor.append(
                observation.observation_id, "retention_expired", fingerprint, context
            )
        self._connection.commit()
        return len(expired)

    def metrics(self) -> LearningMetrics:
        """Return content-free learning observability counters."""
        proposals = self.proposals()
        procedure_proposals = sum(isinstance(proposal, SkillProposal) for proposal in proposals)
        proposed = sum(proposal.status is ProposalStatus.PROPOSED for proposal in proposals)
        approved = sum(proposal.status is ProposalStatus.APPROVED for proposal in proposals)
        corrected = sum(proposal.status is ProposalStatus.CORRECTED for proposal in proposals)
        confidences = tuple(proposal.confidence for proposal in proposals)
        return LearningMetrics(
            observation_count=self._count("SELECT count(*) FROM learning_observations"),
            proposal_count=len(proposals),
            procedure_proposals=procedure_proposals,
            memory_update_proposals=len(proposals) - procedure_proposals,
            proposed=proposed,
            approved=approved,
            corrected=corrected,
            deleted=self._count("SELECT count(*) FROM learning_tombstones"),
            mean_confidence=(
                None if not confidences else round(sum(confidences) / len(confidences), 6)
            ),
        )

    def _read(self, proposal_id: RecordId) -> LearningProposal:
        row = self._connection.execute(
            "SELECT content_json FROM learning_proposals WHERE proposal_id=?", (str(proposal_id),)
        ).fetchone()
        if row is None:
            raise ProposalNotFoundError(proposal_id=proposal_id)
        return LEARNING_PROPOSAL_ADAPTER.validate_json(str(row[0]))

    def _insert(self, proposal: LearningProposal) -> None:
        _ = self._connection.execute(
            "INSERT INTO learning_proposals VALUES(?, ?, ?, ?)",
            (
                str(proposal.proposal_id),
                proposal.status.value,
                proposal.model_dump_json(),
                proposal.created_at.isoformat(),
            ),
        )

    def _exists(self, proposal_id: RecordId) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM learning_proposals WHERE proposal_id=?", (str(proposal_id),)
        ).fetchone()
        return row is not None

    def _suppressed(self, proposal: LearningProposal) -> bool:
        """Return whether a deleted pattern must not be proposed again."""
        fingerprint = self._auditor.fingerprint(_pattern(proposal))
        row = self._connection.execute(
            "SELECT 1 FROM learning_tombstones WHERE pattern_fingerprint=?",
            (fingerprint,),
        ).fetchone()
        return row is not None

    def _replace(
        self,
        current: LearningProposal,
        replacement: LearningProposal,
        action: str,
        context: TransitionContext,
    ) -> None:
        """Swap a proposal revision while retaining only its keyed fingerprint."""
        fingerprint = self._auditor.fingerprint(current.model_dump_json())
        with self._lifecycle.deletion():
            _ = self._connection.execute(
                "DELETE FROM learning_proposals WHERE proposal_id=?",
                (str(current.proposal_id),),
            )
        self._insert(replacement)
        self._auditor.append(current.proposal_id, action, fingerprint, context)

    def _count(self, query: str) -> int:
        row = self._connection.execute(query).fetchone()
        return 0 if row is None else int(row[0])
