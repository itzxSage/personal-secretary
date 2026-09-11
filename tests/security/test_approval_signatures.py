"""Possession, binding, revocation, replay, and execution-side-effect checks."""

from datetime import timedelta
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from secretary_service.authority import (
    PolicyViolationError,
    ProposalLifecycle,
    default_approval_matrix,
)
from secretary_service.enrollment import DeviceRegistry
from secretary_service.fixture_authority import FIXTURE_PUBLIC_KEY, sign_fixture_approval
from secretary_service.models import ActorId, RecordId
from tests.helpers import FakeClock
from tests.test_authority import ACTOR, DEFAULT_DEVICE_ID, make_approval, make_proposal


@pytest.mark.parametrize(
    "mutation", ["unsigned", "attacker_key", "actor", "expiry", "idempotency", "action"]
)
def test_known_device_id_does_not_prove_authorization(clock: FakeClock, mutation: str) -> None:
    devices = DeviceRegistry(clock)
    _ = devices.enroll(
        DEFAULT_DEVICE_ID, "fingerprint", ACTOR, approval_public_key=FIXTURE_PUBLIC_KEY
    )
    lifecycle = ProposalLifecycle(clock, devices)
    proposal = make_proposal(clock, "calendar.apply", "exact approved payload")
    approval = make_approval(clock, proposal)
    if mutation == "unsigned":
        approval = approval.model_copy(update={"signature": ""})
    elif mutation == "attacker_key":
        signature = Ed25519PrivateKey.generate().sign(approval.signing_bytes(proposal.action_class))
        approval = approval.model_copy(update={"signature": signature.hex()})
    elif mutation == "actor":
        approval = approval.model_copy(update={"actor": ActorId("impersonated")})
    elif mutation == "expiry":
        approval = approval.model_copy(update={"expires_at": clock.now() + timedelta(days=1)})
    elif mutation == "idempotency":
        approval = approval.model_copy(update={"idempotency_key": "new-key"})
    else:
        proposal = proposal.model_copy(update={"action_class": "message.send"})
    with pytest.raises(PolicyViolationError, match="signature"):
        _ = lifecycle.approve(proposal, approval, default_approval_matrix())


def test_revocation_after_signing_is_checked_at_approval(clock: FakeClock) -> None:
    devices = DeviceRegistry(clock)
    _ = devices.enroll(
        DEFAULT_DEVICE_ID, "fingerprint", ACTOR, approval_public_key=FIXTURE_PUBLIC_KEY
    )
    lifecycle = ProposalLifecycle(clock, devices)
    proposal = make_proposal(clock, "calendar.apply", "payload")
    approval = make_approval(clock, proposal)
    _ = devices.revoke(DEFAULT_DEVICE_ID, ACTOR)
    with pytest.raises(PolicyViolationError, match="active enrolled"):
        _ = lifecycle.approve(proposal, approval, default_approval_matrix())


def test_resigning_a_new_fact_cannot_repeat_approval(clock: FakeClock) -> None:
    devices = DeviceRegistry(clock)
    _ = devices.enroll(
        DEFAULT_DEVICE_ID, "fingerprint", ACTOR, approval_public_key=FIXTURE_PUBLIC_KEY
    )
    lifecycle = ProposalLifecycle(clock, devices)
    proposal = make_proposal(clock, "calendar.apply", "payload")
    approval = make_approval(clock, proposal)
    _ = lifecycle.approve(proposal, approval, default_approval_matrix())
    replay = sign_fixture_approval(approval.model_copy(update={"fact_id": RecordId(uuid4())}))
    with pytest.raises(PolicyViolationError, match="replay"):
        _ = lifecycle.approve(proposal, replay, default_approval_matrix())
