"""Governed learning, procedural-skill proposal, and observability tests."""

from datetime import timedelta

import pytest

from secretary_service.learning.models import (
    ActualOutcome,
    LearningMetrics,
    MemoryUpdateProposal,
    ProposalCorrection,
    ProposalDeletion,
    ProposalStatus,
    SkillProposal,
)
from secretary_service.memory import MemoryCategory, RetrievalScope
from secretary_service.storage import EncryptedStateStore
from tests.helpers import FakeClock
from tests.learning_helpers import (
    WORK_SCHEDULE_STEPS,
    WORK_SCHEDULE_TRIGGER,
    context,
    divergent_observation,
    observation,
)


def test_repeated_completed_outcomes_propose_but_never_activate_a_skill(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    """Repeated fixture outcomes propose a skill without activating it."""
    transition = context(clock, "corr-learning-propose")
    for suffix in range(1, 6):
        store.learning.record_observation(observation(clock, suffix), transition)

    (proposal,) = store.learning.learn(transition)

    assert isinstance(proposal, SkillProposal)
    assert proposal.status is ProposalStatus.PROPOSED
    assert proposal.trigger == WORK_SCHEDULE_TRIGGER
    assert proposal.procedure == WORK_SCHEDULE_STEPS
    assert proposal.confidence >= 0.6
    assert store.learning.route_candidates() == ()


def test_sparse_evidence_produces_no_proposal(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    """Fewer than the minimum observations propose nothing."""
    transition = context(clock, "corr-learning-sparse")
    for suffix in range(1, 3):
        store.learning.record_observation(observation(clock, suffix), transition)

    assert store.learning.learn(transition) == ()


def test_explicit_approval_is_the_only_route_to_routing(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    """Only an explicit approval makes a proposal routable."""
    transition = context(clock, "corr-learning-approve")
    for suffix in range(1, 4):
        store.learning.record_observation(observation(clock, suffix), transition)
    (proposal,) = store.learning.learn(transition)

    assert store.learning.route_candidates() == ()
    approved = store.learning.approve(proposal.proposal_id, transition)

    assert approved.status is ProposalStatus.APPROVED
    assert store.learning.route_candidates() == (approved,)


def test_correction_removes_a_proposal_from_future_routing(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    """A corrected proposal leaves routing and is never re-proposed."""
    transition = context(clock, "corr-learning-correct")
    for suffix in range(1, 4):
        store.learning.record_observation(observation(clock, suffix), transition)
    (proposal,) = store.learning.learn(transition)
    _ = store.learning.approve(proposal.proposal_id, transition)

    corrected = store.learning.correct(
        proposal.proposal_id,
        ProposalCorrection(
            expected_status=ProposalStatus.APPROVED,
            note="drop the commute step",
            procedure=WORK_SCHEDULE_STEPS[:-1],
        ),
        transition,
    )

    assert corrected.status is ProposalStatus.CORRECTED
    assert isinstance(corrected, SkillProposal)
    assert corrected.procedure == WORK_SCHEDULE_STEPS[:-1]
    assert store.learning.route_candidates() == ()
    assert store.learning.learn(transition) == ()


def test_deletion_removes_a_proposal_and_suppresses_reproposal(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    """A deleted proposal is erased and its pattern is not proposed again."""
    transition = context(clock, "corr-learning-delete")
    for suffix in range(1, 4):
        store.learning.record_observation(observation(clock, suffix), transition)
    (proposal,) = store.learning.learn(transition)

    store.learning.delete(
        proposal.proposal_id,
        ProposalDeletion(expected_status=ProposalStatus.PROPOSED),
        transition,
    )

    assert store.learning.proposals() == ()
    assert store.learning.learn(transition) == ()


def test_divergent_outcomes_propose_an_explainable_memory_update(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    """Repeated abandoned outcomes propose an explainable memory update."""
    transition = context(clock, "corr-learning-memory")
    for suffix in range(1, 4):
        store.learning.record_observation(divergent_observation(clock, suffix), transition)

    (proposal,) = store.learning.learn(transition)

    assert isinstance(proposal, MemoryUpdateProposal)
    assert proposal.category is MemoryCategory.OBSERVATION
    assert proposal.content == "Deep work is usually abandoned after a physical shift"
    assert proposal.status is ProposalStatus.PROPOSED


def test_learning_never_writes_governed_memory(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    """Learning proposals never bypass governed memory persistence."""
    transition = context(clock, "corr-learning-no-memory")
    for suffix in range(1, 4):
        store.learning.record_observation(divergent_observation(clock, suffix), transition)
    _ = store.learning.learn(transition)

    assert store.memory.retrieve(RetrievalScope.PRIVATE) == ()


def test_metrics_are_content_free(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    """Observability metrics expose counts without sensitive content."""
    transition = context(clock, "corr-learning-metrics")
    for suffix in range(1, 4):
        store.learning.record_observation(observation(clock, suffix), transition)
    _ = store.learning.learn(transition)

    metrics = store.learning.metrics()

    assert isinstance(metrics, LearningMetrics)
    assert metrics.observation_count == 3
    assert metrics.proposal_count == 1
    assert metrics.procedure_proposals == 1
    assert metrics.memory_update_proposals == 0
    assert metrics.proposed == 1
    assert metrics.approved == 0
    assert metrics.corrected == 0
    assert metrics.deleted == 0
    assert metrics.mean_confidence == 0.6


def test_retention_expiry_excludes_and_purges_observations(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    """Expired observations leave retrieval and are purged with fingerprints."""
    transition = context(clock, "corr-learning-retention")
    expired = observation(clock, 1).model_copy(
        update={"retain_until": clock.now() - timedelta(days=1)}
    )
    live = observation(clock, 2).model_copy(
        update={"retain_until": clock.now() + timedelta(days=1)}
    )
    store.learning.record_observation(expired, transition)
    store.learning.record_observation(live, transition)

    assert len(store.learning.observations()) == 1
    assert store.learning.purge_expired(transition) == 1
    assert len(store.learning.observations()) == 1


def test_stale_correction_and_mismatched_payload_are_rejected(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    """Stale status guards and kind-mismatched payloads are rejected."""
    transition = context(clock, "corr-learning-stale")
    for suffix in range(1, 4):
        store.learning.record_observation(observation(clock, suffix), transition)
    (proposal,) = store.learning.learn(transition)

    with pytest.raises(Exception, match="expected status"):
        _ = store.learning.correct(
            proposal.proposal_id,
            ProposalCorrection(
                expected_status=ProposalStatus.APPROVED,
                note="stale",
                procedure=WORK_SCHEDULE_STEPS,
            ),
            transition,
        )
    with pytest.raises(Exception, match="does not match proposal kind"):
        _ = store.learning.correct(
            proposal.proposal_id,
            ProposalCorrection(
                expected_status=ProposalStatus.PROPOSED,
                note="wrong payload",
                content="not a procedure",
            ),
            transition,
        )


def test_observation_roundtrip_preserves_planned_vs_actual_fields(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    """An observation roundtrip preserves planned and actual fields."""
    transition = context(clock, "corr-learning-roundtrip")
    sample = observation(clock, 7)
    store.learning.record_observation(sample, transition)

    (restored,) = store.learning.observations()

    assert restored == sample
    assert restored.planned.activity_id == sample.planned.activity_id
    assert restored.outcome is ActualOutcome.COMPLETED
    assert restored.procedure_steps == WORK_SCHEDULE_STEPS


def test_learning_audit_entries_are_content_minimized(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    """Learning audit entries never embed observation or proposal content."""
    transition = context(clock, "corr-learning-audit")
    for suffix in range(1, 4):
        store.learning.record_observation(observation(clock, suffix), transition)
    _ = store.learning.learn(transition)

    entries = store.audit_entries()

    assert [entry.action_class for entry in entries] == [
        "learning.observed",
        "learning.observed",
        "learning.observed",
        "learning.proposed",
    ]
    assert all("Deep work" not in entry.action_metadata for entry in entries)
    assert all("extract date" not in entry.action_metadata for entry in entries)
    assert store.verify_audit_chain().entries_verified == 4
