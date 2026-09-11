"""Explicit consent gate for owner-controlled OpenClaw channel bindings."""

from enum import StrEnum
from typing import ClassVar, final, override
from uuid import UUID

from pydantic import ConfigDict

from secretary_service.conversation_api import ChannelKind
from secretary_service.models import FrozenModel, NonEmpty
from secretary_service.openclaw.bindings import IdentityBindingRegistry
from secretary_service.openclaw.contracts import (
    BindingEnrollment,
    BoundChannelIdentity,
    MtlsClientIdentity,
)


class ChannelConsentRejection(StrEnum):
    """Closed consent failures represented as stable string values."""

    DECLINED = "declined"
    IDENTITY_MISMATCH = "identity_mismatch"


class ChannelBindingConsent(FrozenModel):
    """Owner decision bound to one exact external identity enrollment."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    binding_id: UUID
    channel_kind: ChannelKind
    external_channel_id: NonEmpty
    external_user_id: NonEmpty
    granted: bool

    @classmethod
    def from_identity(
        cls, identity: BoundChannelIdentity, *, granted: bool
    ) -> "ChannelBindingConsent":
        """Create consent tied to every external identity field."""
        binding = identity.binding
        return cls(
            binding_id=binding.binding_id,
            channel_kind=binding.channel_kind,
            external_channel_id=binding.external_channel_id,
            external_user_id=binding.external_user_id,
            granted=granted,
        )


@final
class ChannelConsentError(Exception):
    """Channel enrollment lacked exact affirmative owner consent."""

    def __init__(self, binding_id: UUID, reason: ChannelConsentRejection) -> None:
        """Record the binding and stable consent rejection reason."""
        super().__init__(binding_id, reason)
        self.binding_id = binding_id
        self.reason = reason

    @override
    def __str__(self) -> str:
        return f"binding {self.binding_id} consent rejected: {self.reason}"


@final
class ChannelBindingCoordinator:
    """Require explicit consent before delegating to the one-shot registry."""

    def __init__(self, bindings: IdentityBindingRegistry) -> None:
        """Bind the consent gate to the owner-controlled registry."""
        self._bindings = bindings

    def enroll(
        self,
        enrollment: BindingEnrollment,
        consent: ChannelBindingConsent,
        approver: MtlsClientIdentity,
    ) -> BoundChannelIdentity:
        """Enroll only after exact affirmative consent and owner mTLS verification."""
        self.enforce_consent(enrollment.identity, consent)
        return self._bindings.enroll(enrollment, approver)

    @staticmethod
    def enforce_consent(
        identity: BoundChannelIdentity,
        consent: ChannelBindingConsent,
    ) -> None:
        """Fail closed unless consent grants the exact identity tuple."""
        binding = identity.binding
        if not consent.granted:
            raise ChannelConsentError(binding.binding_id, ChannelConsentRejection.DECLINED)
        matches = (
            consent.binding_id == binding.binding_id
            and consent.channel_kind == binding.channel_kind
            and consent.external_channel_id == binding.external_channel_id
            and consent.external_user_id == binding.external_user_id
        )
        if not matches:
            raise ChannelConsentError(
                binding.binding_id,
                ChannelConsentRejection.IDENTITY_MISMATCH,
            )
