"""Adversarial tests for the isolated OpenClaw compatibility boundary."""

import pytest

from secretary_service.conversation_api import ChannelKind
from secretary_service.enrollment import ForgedDeviceError
from secretary_service.openclaw.bindings import (
    BindingRejection,
    BindingViolationError,
)
from secretary_service.openclaw.contracts import (
    BindingEnrollment,
    MtlsClientIdentity,
    OpenClawReadRequest,
    OpenClawTextEnvelope,
)
from tests.helpers import FakeClock
from tests.test_openclaw_adapter import (
    BINDING_ID,
    CONVERSATION_ID,
    OWNER,
    SERVICE,
    SERVICE_ID,
    make_adapter,
    make_event,
)


def test_malicious_plugin_cannot_enumerate_life_engine_records(clock: FakeClock) -> None:
    adapter, _, _, _ = make_adapter(clock, ChannelKind.OPENCLAW_WEBCHAT)

    decision = adapter.endpoint_access("life_records.enumerate")

    assert decision.allowed is False
    assert decision.endpoint not in {item.value for item in adapter.allowed_endpoints}
    assert not hasattr(adapter, "canonical_repository")
    assert not hasattr(adapter, "filesystem")
    assert not hasattr(adapter, "key_provider")


def test_malicious_plugin_cannot_forge_bound_channel_identity(clock: FakeClock) -> None:
    adapter, _, claim, _ = make_adapter(clock, ChannelKind.TELEGRAM)
    forged = claim.model_copy(update={"external_user_id": "attacker"})

    with pytest.raises(BindingViolationError) as caught:
        _ = adapter.events(request=OpenClawReadRequest(claim=forged), client=SERVICE)

    assert caught.value.reason == BindingRejection.IDENTITY_MISMATCH


def test_binding_enrollment_and_revocation_cannot_be_replayed(clock: FakeClock) -> None:
    adapter, _, claim, bindings = make_adapter(clock, ChannelKind.OPENCLAW_WEBCHAT)
    enrollment = BindingEnrollment(identity=bindings.resolve(claim, SERVICE_ID))

    with pytest.raises(BindingViolationError) as duplicate:
        _ = bindings.enroll(enrollment, OWNER)
    assert duplicate.value.reason == BindingRejection.REPLAYED

    _ = bindings.revoke(BINDING_ID, OWNER)
    with pytest.raises(BindingViolationError) as revoked:
        _ = adapter.events(OpenClawReadRequest(claim=claim), SERVICE)
    assert revoked.value.reason == BindingRejection.REVOKED
    with pytest.raises(BindingViolationError) as replayed:
        _ = bindings.enroll(enrollment, OWNER)
    assert replayed.value.reason == BindingRejection.REPLAYED


def test_malicious_plugin_cannot_escalate_capability(clock: FakeClock) -> None:
    adapter, _, _, _ = make_adapter(clock, ChannelKind.TELEGRAM)

    for capability in ("database.read", "filesystem.read", "keys.read", "worker.execute"):
        decision = adapter.capability_access(capability)
        assert decision.allowed is False


def test_forged_service_account_mtls_identity_fails_before_api_access(clock: FakeClock) -> None:
    adapter, api, claim, _ = make_adapter(clock, ChannelKind.TELEGRAM)
    forged = MtlsClientIdentity(
        device_id=SERVICE_ID,
        public_key_fingerprint="forged-fingerprint",
    )

    with pytest.raises(ForgedDeviceError):
        _ = adapter.events(OpenClawReadRequest(claim=claim), forged)

    assert api.list_events(CONVERSATION_ID) == ()


def test_conversation_api_outage_leaves_canonical_state_intact(clock: FakeClock) -> None:
    adapter, api, claim, _ = make_adapter(clock, ChannelKind.OPENCLAW_WEBCHAT)
    original = api.list_events(CONVERSATION_ID)
    api.set_unavailable()
    envelope = OpenClawTextEnvelope(
        claim=claim,
        event=make_event(clock),
    )

    with pytest.raises(ConnectionError):
        _ = adapter.append_text(envelope, SERVICE)

    assert api.list_events(CONVERSATION_ID) == original
