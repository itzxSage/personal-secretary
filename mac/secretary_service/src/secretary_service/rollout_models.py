"""Typed contracts for sandbox-only rollout staging."""

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, final, override

from pydantic import ConfigDict

from secretary_service.authority import Approval, ProposalRecord
from secretary_service.enrollment import DeviceRegistry
from secretary_service.leases import LeaseIssuer
from secretary_service.models import FrozenModel, RecordId, TransitionContext
from secretary_service.storage import Clock, EncryptedStateStore

ROLLOUT_CAPABILITY = "rollout.stage"
ROLLOUT_WORKER = "lifeos-rollout"


class RolloutTarget(StrEnum):
    """Closed rollout destinations; Task 18 accepts only sandbox apply."""

    SANDBOX = "sandbox"
    PRODUCTION = "production"


class RolloutPlan(FrozenModel):
    """Untrusted rollout fixture parsed before staging begins."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    rollout_id: RecordId
    target: RolloutTarget
    pinned_revision: str
    candidate_revision: str
    expected_endpoints: tuple[str, ...]
    expected_capabilities: tuple[str, ...]
    reversible: bool
    fabric_enabled: bool
    workers_enabled: bool
    production_capabilities_enabled: bool


class RolloutPreview(FrozenModel):
    """Deterministic dry-run result containing no secret or mutable state."""

    rollout_id: RecordId
    target: RolloutTarget
    candidate_revision: str
    revision_compatible: bool
    endpoints_unchanged: bool
    capabilities_unchanged: bool
    reversible: bool
    fabric_enabled: bool
    workers_enabled: bool
    production_capabilities_enabled: bool
    external_operations: int

    def canonical_payload(self) -> str:
        """Return stable JSON for approval and lease binding."""
        return json.dumps(self.model_dump(mode="json"), separators=(",", ":"), sort_keys=True)


class RolloutResult(FrozenModel):
    """Audited stage result with explicit fail-closed capability state."""

    proposal: ProposalRecord
    fabric_enabled: bool
    workers_enabled: bool
    production_capabilities_enabled: bool
    audit_entries: int


@final
class RolloutViolationError(Exception):
    """A rollout attempted an incompatible or production-affecting transition."""

    def __init__(self, reason: str) -> None:
        """Record a machine-readable rollout denial reason."""
        super().__init__(reason)
        self.reason = reason

    @override
    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class RolloutDependencies:
    """State and authority boundaries required by staged rollout."""

    store: EncryptedStateStore
    devices: DeviceRegistry
    clock: Clock
    leases: LeaseIssuer


@dataclass(frozen=True, slots=True)
class RolloutApprovalRequest:
    """Device proof and audit context for one explicit rollout approval."""

    approval: Approval
    fingerprint: str
    context: TransitionContext
