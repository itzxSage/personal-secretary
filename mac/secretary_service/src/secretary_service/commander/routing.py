"""Pure deterministic Commander delegation routing."""

from secretary_service.commander.contracts import (
    DelegationCase,
    DelegationDecision,
    DelegationTarget,
)


def route_delegation(case: DelegationCase) -> DelegationDecision:
    """Choose Jared, autonomous agent work, or collaboration from typed facts."""
    if case.requires_jared_judgment:
        return DelegationDecision(
            target=DelegationTarget.JARED,
            reason="jared_judgment_required",
        )
    if case.independently_executable and case.scope_is_complete:
        return DelegationDecision(
            target=DelegationTarget.AGENT,
            reason="bounded_independent_execution",
        )
    return DelegationDecision(
        target=DelegationTarget.ASSIST,
        reason="collaboration_required",
    )
