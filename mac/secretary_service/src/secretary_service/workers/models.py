"""Immutable controls and audit records for isolated workers."""

from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import ClassVar
from uuid import UUID

from pydantic import ConfigDict, Field

from secretary_service.commander import RuntimeDescriptor, WorkerCapability
from secretary_service.models import CorrelationId, FrozenModel, NonEmpty, RecordId


class EffectStatus(StrEnum):
    """Lifecycle of a reversible sandbox effect."""

    APPLIED = "applied"
    COMPENSATED = "compensated"
    RECONCILED = "reconciled"


class WorkerGateStatus(StrEnum):
    """Content-minimized reconciliation outcomes."""

    MATCHED = "matched"
    MISSING = "missing"


class SandboxLimits(FrozenModel):
    """Closed resource and request ceilings for one sandbox runtime."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    filesystem_roots: tuple[Path, ...]
    allowed_sites: tuple[NonEmpty, ...]
    max_attempts: int = Field(default=1, ge=1, le=5)
    max_timeout_seconds: int = Field(ge=1, le=3600)
    max_tokens: int = Field(ge=1)
    max_cost_microunits: int = Field(ge=0)
    credential_ttl: timedelta = Field(gt=timedelta(0), le=timedelta(minutes=5))


class ArtifactProvenance(FrozenModel):
    """Traceability for a sandbox-produced artifact without raw content."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    reference: NonEmpty
    request_id: UUID
    worker_id: NonEmpty
    capability: WorkerCapability
    source: NonEmpty
    source_id: NonEmpty
    correlation_id: CorrelationId
    created_at: datetime
    payload_sha256: NonEmpty
    runtime: RuntimeDescriptor


class WorkerEffect(FrozenModel):
    """Reversible in-sandbox effect with compensation evidence."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    effect_id: RecordId
    request_id: UUID
    artifact_reference: NonEmpty
    status: EffectStatus
    reversible: bool
    reconciliation: WorkerGateStatus | None = None
    updated_at: datetime
