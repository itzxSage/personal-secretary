"""Consent, provider retention, and Keychain root-key rotation policy."""

from datetime import datetime
from enum import StrEnum
from typing import ClassVar, Final, Protocol, final

from pydantic import ConfigDict

from secretary_service.models import FrozenModel, NonEmpty
from secretary_service.voice.errors import RecoveryEnvelopeError
from secretary_service.voice.models import (
    ROOT_KEY_ROTATION_INTERVAL,
    ProviderRetentionType,
    VoiceSurface,
)

SAFE_PROVIDER_RETENTION: Final = frozenset(
    {
        ProviderRetentionType.ZERO_DATA_RETENTION,
        ProviderRetentionType.MODIFIED_ABUSE_MONITORING,
    }
)


class ConsentRecord(FrozenModel):
    """Current explicit grant state for one invocation surface."""

    surface: VoiceSurface
    granted_at: datetime
    withdrawn_at: datetime | None = None


@final
class ConsentRegistry:
    """Mutable current-consent projection over persisted consent facts."""

    def __init__(self) -> None:
        """Create an empty fail-closed consent projection."""
        self._records: dict[VoiceSurface, ConsentRecord] = {}

    def grant(self, surface: VoiceSurface, granted_at: datetime) -> None:
        """Grant provider use for exactly one surface."""
        self._records[surface] = ConsentRecord(surface=surface, granted_at=granted_at)

    def withdraw(self, surface: VoiceSurface, withdrawn_at: datetime) -> None:
        """Withdraw one surface grant so later sessions fail closed."""
        current = self._records.get(surface)
        if current is None:
            return
        self._records[surface] = current.model_copy(update={"withdrawn_at": withdrawn_at})

    def is_granted(self, surface: VoiceSurface, now: datetime) -> bool:
        """Return whether the surface has an active explicit grant."""
        record = self._records.get(surface)
        return record is not None and record.granted_at <= now and record.withdrawn_at is None


class RetentionVerification(FrozenModel):
    """Time-bounded verification of the current OpenAI project controls."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    project_id: NonEmpty
    retention_type: ProviderRetentionType
    store: bool
    verified_at: datetime
    valid_until: datetime

    def authorizes(self, project_id: str, now: datetime) -> bool:
        """Require store:false and a current privacy-qualified project setting."""
        if (
            self.project_id != project_id
            or self.store
            or not (self.verified_at <= now < self.valid_until)
        ):
            return False
        return self.retention_type in SAFE_PROVIDER_RETENTION


class KeyRotationReason(StrEnum):
    """Closed reasons that may rotate the SQLCipher root key."""

    SCHEDULED = "scheduled"
    COMPROMISE = "compromise"


class KeychainRootKeyHandle(FrozenModel):
    """Opaque Keychain metadata; root key bytes are intentionally absent."""

    reference: NonEmpty
    generation: int
    activated_at: datetime


class OfflineRecoveryEnvelope(FrozenModel):
    """Evidence of the existing sealed offline recovery-envelope procedure."""

    reference: NonEmpty
    created_at: datetime
    offline: bool
    sealed: bool
    audit_chain_verified: bool


class KeychainRootKeyCustodian(Protocol):
    """Local Keychain operation that never exposes root key material."""

    def rotate_database_key(
        self,
        current_reference: str,
        replacement_reference: str,
        recovery_envelope_reference: str,
    ) -> None:
        """Atomically rotate local SQLCipher custody by opaque references."""
        ...


class KeyRotationPlan(FrozenModel):
    """Inputs for one scheduled or compromise-triggered key rotation."""

    replacement_reference: NonEmpty
    envelope: OfflineRecoveryEnvelope
    reason: KeyRotationReason
    now: datetime


@final
class KeyRotationCoordinator:
    """Enforce 90-day/compromise rotation through an offline recovery envelope."""

    def __init__(self, custodian: KeychainRootKeyCustodian) -> None:
        """Bind rotation policy to the local opaque-reference custodian."""
        self._custodian = custodian

    def rotate(
        self, current: KeychainRootKeyHandle, plan: KeyRotationPlan
    ) -> KeychainRootKeyHandle:
        """Rotate only when due and backed by verified offline recovery."""
        envelope = plan.envelope
        if not (envelope.offline and envelope.sealed and envelope.audit_chain_verified):
            raise RecoveryEnvelopeError(reference=envelope.reference)
        scheduled = plan.reason is KeyRotationReason.SCHEDULED
        if scheduled and current.activated_at + ROOT_KEY_ROTATION_INTERVAL > plan.now:
            message = "scheduled root-key rotation is not due"
            raise ValueError(message)
        self._custodian.rotate_database_key(
            current.reference,
            plan.replacement_reference,
            envelope.reference,
        )
        return KeychainRootKeyHandle(
            reference=plan.replacement_reference,
            generation=current.generation + 1,
            activated_at=plan.now,
        )
