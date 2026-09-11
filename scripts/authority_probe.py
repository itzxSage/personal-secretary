#!/usr/bin/env python3
"""Manual QA probe: forged/replay requests against the real authority policy path.

Runs adversarial requests through the actual authority, enrollment, and lease
modules, prints the denied/audit output, and removes all temporary credentials
before exiting. Exit 0 only when every adversarial request is denied as expected.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, final
from uuid import uuid4

from secretary_service.authority import (
    Approval,
    ApprovalMatrix,
    ApprovalProof,
    PolicyViolationError,
    ProposalLifecycle,
    ProposalRecord,
    ProposalState,
    default_approval_matrix,
)
from secretary_service.enrollment import (
    DeviceId,
    DeviceRegistry,
    ForgedDeviceError,
    RevokedDeviceError,
)
from secretary_service.fixture_authority import FIXTURE_PUBLIC_KEY, sign_fixture_approval
from secretary_service.leases import LeaseIssuer, LeaseRequest, LeaseViolationError, SigningKeyRing
from secretary_service.models import ActorId, CorrelationId, RecordId

PROBE_ACTOR: Final = ActorId("qa-probe")
PROBE_CORRELATION: Final = CorrelationId("corr-qa-probe")


@final
class ProbeClock:
    """Deterministic UTC clock for the probe run."""

    def __init__(self, current: datetime) -> None:
        """Initialize the deterministic clock at a fixed instant."""
        self._current = current

    def now(self) -> datetime:
        """Return the current probe time."""
        return self._current

    def advance(self, delta: timedelta) -> None:
        """Advance the probe clock by a fixed delta."""
        self._current += delta


def _proposal(clock: ProbeClock, action_class: str, payload: str) -> ProposalRecord:
    """Build a proposed record for one probe action."""
    return ProposalRecord(
        proposal_id=RecordId(uuid4()),
        action_class=action_class,
        payload=payload,
        state=ProposalState.PROPOSED,
        created_at=clock.now(),
    )


def _approve(
    clock: ProbeClock,
    lifecycle: ProposalLifecycle,
    matrix: ApprovalMatrix,
    proposal: ProposalRecord,
) -> ProposalRecord:
    approval = Approval(
        fact_id=RecordId(uuid4()),
        proposal_id=proposal.proposal_id,
        payload_hash=proposal.payload_hash(),
        proof=ApprovalProof.DEVICE_SIGNED,
        device_id=DeviceId("qa-iphone"),
        issued_at=clock.now(),
        expires_at=clock.now() + timedelta(minutes=5),
        idempotency_key="qa-approval",
        actor=PROBE_ACTOR,
        correlation_id=PROBE_CORRELATION,
    )
    lifecycle.approve(proposal, sign_fixture_approval(approval), matrix)
    return proposal.model_copy(update={"state": ProposalState.APPROVED})


def main() -> int:
    """Run adversarial probes and report denied requests."""
    workdir = Path(tempfile.mkdtemp(prefix="authority-probe-"))
    try:
        clock = ProbeClock(datetime(2026, 9, 6, tzinfo=UTC))
        matrix = default_approval_matrix()
        ring = SigningKeyRing(b"qa-signing-key-1")
        issuer = LeaseIssuer(clock, ring, timedelta(minutes=10))
        registry = DeviceRegistry(clock)
        registry.enroll(
            DeviceId("qa-iphone"),
            "fp-qa-iphone",
            PROBE_ACTOR,
            approval_public_key=FIXTURE_PUBLIC_KEY,
        )
        lifecycle = ProposalLifecycle(clock, registry)

        denied: list[str] = []

        # Probe 1: forged device cannot verify mTLS identity.
        try:
            _ = registry.verify_mtls_identity(DeviceId("qa-iphone"), "fp-forged")
        except ForgedDeviceError as error:
            denied.append(f"forged device denied: {error}")
        else:
            print("FAIL: forged device identity was accepted")
            return 1

        # Probe 2: interrupted spoken approval cannot approve.
        proposal = _proposal(clock, "calendar.apply", "move workout to 6pm")
        spoken = Approval(
            fact_id=RecordId(uuid4()),
            proposal_id=proposal.proposal_id,
            payload_hash=proposal.payload_hash(),
            proof=ApprovalProof.VOICE,
            device_id=None,
            issued_at=clock.now(),
            expires_at=clock.now() + timedelta(minutes=5),
            idempotency_key="qa-spoken",
            actor=PROBE_ACTOR,
            correlation_id=PROBE_CORRELATION,
        )
        try:
            _ = lifecycle.approve(proposal, spoken, matrix)
        except PolicyViolationError as error:
            denied.append(f"interrupted spoken approval denied: {error}")
        else:
            print("FAIL: interrupted spoken approval was accepted")
            return 1

        # Probe 3: replay of a consumed lease is denied.
        approved = _approve(clock, lifecycle, matrix, proposal)
        lease = issuer.issue(
            approved,
            LeaseRequest(
                capability="calendar.apply",
                worker_id="qa-worker",
                actor=PROBE_ACTOR,
                correlation_id=PROBE_CORRELATION,
                idempotency_key="qa-lease",
            ),
        )
        _ = issuer.verify(lease, approved)
        try:
            _ = issuer.verify(lease, approved)
        except LeaseViolationError as error:
            denied.append(f"replayed lease denied: {error}")
        else:
            print("FAIL: replayed lease was accepted")
            return 1

        # Probe 4: revoked device cannot reconnect.
        registry.revoke(DeviceId("qa-iphone"), PROBE_ACTOR)
        try:
            _ = registry.verify_mtls_identity(DeviceId("qa-iphone"), "fp-qa-iphone")
        except RevokedDeviceError as error:
            denied.append(f"revoked device denied: {error}")
        else:
            print("FAIL: revoked device reconnected")
            return 1

        for line in denied:
            print(f"denied: {line}")
        print(f"authority probe ok: {len(denied)} adversarial requests denied")
        return 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        print(f"cleaned temporary credentials: {workdir}")


if __name__ == "__main__":
    sys.exit(main())
