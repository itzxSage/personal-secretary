"""Owner-approved OpenClaw identity binding enrollment and revocation."""

from enum import StrEnum
from typing import final, override
from uuid import UUID

from secretary_service.conversation_api import ChannelBindingState
from secretary_service.enrollment import DeviceId, DeviceRegistry, DeviceState
from secretary_service.openclaw.contracts import (
    INITIAL_TEXT_CHANNELS,
    BindingEnrollment,
    BoundChannelIdentity,
    ChannelIdentityClaim,
    MtlsClientIdentity,
)


class BindingRejection(StrEnum):
    """Closed reasons an identity-binding operation fails."""

    APPROVER_REQUIRED = "approver_required"
    SERVICE_ACCOUNT_UNKNOWN = "service_account_unknown"
    UNSUPPORTED_CHANNEL = "unsupported_channel"
    REPLAYED = "replayed"
    UNKNOWN = "unknown"
    REVOKED = "revoked"
    IDENTITY_MISMATCH = "identity_mismatch"


@final
class BindingViolationError(Exception):
    """Channel binding enrollment, resolution, or revocation was denied."""

    def __init__(self, binding_id: UUID, reason: BindingRejection) -> None:
        """Record the denied binding and its closed rejection reason."""
        super().__init__(binding_id, reason)
        self.binding_id = binding_id
        self.reason = reason

    @override
    def __str__(self) -> str:
        return f"binding {self.binding_id}: {self.reason.value}"


@final
class IdentityBindingRegistry:
    """Mutable owner-controlled registry for one-shot channel identity bindings."""

    def __init__(self, devices: DeviceRegistry, owner_device_id: DeviceId) -> None:
        """Track one-shot bindings under the owner's enrolled mTLS identity."""
        self._devices = devices
        self._owner_device_id = owner_device_id
        self._bindings: dict[UUID, BoundChannelIdentity] = {}
        self._used_binding_ids: set[UUID] = set()
        self._used_external_identities: set[tuple[str, str, str]] = set()

    def enroll(
        self,
        enrollment: BindingEnrollment,
        approver: MtlsClientIdentity,
    ) -> BoundChannelIdentity:
        """Enroll an initial text binding once under the owner's mTLS identity."""
        binding = enrollment.identity.binding
        self._verify_owner(binding.binding_id, approver)
        service = self._devices.device(enrollment.identity.service_account_device_id)
        if service is None or service.state != DeviceState.ENROLLED:
            raise BindingViolationError(
                binding.binding_id,
                BindingRejection.SERVICE_ACCOUNT_UNKNOWN,
            )
        if binding.channel_kind not in INITIAL_TEXT_CHANNELS:
            raise BindingViolationError(binding.binding_id, BindingRejection.UNSUPPORTED_CHANNEL)
        external_identity = (
            binding.channel_kind.value,
            binding.external_channel_id,
            binding.external_user_id,
        )
        if (
            binding.binding_id in self._used_binding_ids
            or external_identity in self._used_external_identities
        ):
            raise BindingViolationError(binding.binding_id, BindingRejection.REPLAYED)
        if binding.state != ChannelBindingState.ACTIVE:
            raise BindingViolationError(binding.binding_id, BindingRejection.REVOKED)
        self._bindings[binding.binding_id] = enrollment.identity
        self._used_binding_ids.add(binding.binding_id)
        self._used_external_identities.add(external_identity)
        return enrollment.identity

    def revoke(self, binding_id: UUID, approver: MtlsClientIdentity) -> BoundChannelIdentity:
        """Revoke a binding under the owner's enrolled mTLS identity."""
        self._verify_owner(binding_id, approver)
        identity = self._bindings.get(binding_id)
        if identity is None:
            raise BindingViolationError(binding_id, BindingRejection.UNKNOWN)
        revoked = identity.model_copy(
            update={
                "binding": identity.binding.model_copy(
                    update={"state": ChannelBindingState.REVOKED}
                )
            }
        )
        self._bindings[binding_id] = revoked
        return revoked

    def resolve(
        self,
        claim: ChannelIdentityClaim,
        service_account_device_id: DeviceId,
    ) -> BoundChannelIdentity:
        """Resolve an exact active external identity for one service account."""
        identity = self._bindings.get(claim.binding_id)
        if identity is None:
            raise BindingViolationError(claim.binding_id, BindingRejection.UNKNOWN)
        if identity.binding.state != ChannelBindingState.ACTIVE:
            raise BindingViolationError(claim.binding_id, BindingRejection.REVOKED)
        binding = identity.binding
        matches = (
            binding.channel_kind == claim.channel_kind
            and binding.external_channel_id == claim.external_channel_id
            and binding.external_user_id == claim.external_user_id
            and identity.service_account_device_id == service_account_device_id
        )
        if not matches:
            raise BindingViolationError(claim.binding_id, BindingRejection.IDENTITY_MISMATCH)
        return identity

    def _verify_owner(self, binding_id: UUID, approver: MtlsClientIdentity) -> None:
        if approver.device_id != self._owner_device_id:
            raise BindingViolationError(binding_id, BindingRejection.APPROVER_REQUIRED)
        _ = self._devices.verify_mtls_identity(
            approver.device_id,
            approver.public_key_fingerprint,
        )
