"""Typed contracts for governed Commander delegation and fabric workers."""

import json
from datetime import datetime
from enum import StrEnum
from typing import Annotated, ClassVar, Protocol, Self
from uuid import UUID

from pydantic import ConfigDict, Field, StringConstraints, model_validator

from secretary_service.authority import CapabilityLease, ProposalRecord
from secretary_service.models import ActorId, CorrelationId, FrozenModel, NonEmpty


class DelegationTarget(StrEnum):
    """Closed destinations for a Commander delegation decision."""

    JARED = "jared"
    AGENT = "agent"
    ASSIST = "assist"


class WorkerCapability(StrEnum):
    """Closed worker capabilities available to replaceable fabric runtimes."""

    CODE = "code.execute"
    BROWSER = "browser.execute"
    COMPUTER = "computer.execute"
    DOCUMENT = "document.execute"
    EXTERNAL_SEND = "message.send"


class WorkerRuntimeClass(StrEnum):
    """Execution boundary declared by a configured worker runtime."""

    NOOP = "noop"
    SANDBOX = "sandbox"
    LIVE = "live"


class WorkerResultStatus(StrEnum):
    """Closed Commander outcomes after constrained dispatch."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRY_EXHAUSTED = "retry_exhausted"
    DENIED = "denied"
    UNAVAILABLE = "unavailable"


class WorkerAttemptStatus(StrEnum):
    """Closed outcomes returned by one fabric runtime attempt."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRY = "retry"


class DelegationCase(FrozenModel):
    """Deterministic facts used to choose Jared, an agent, or assisted work."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    requires_jared_judgment: bool
    independently_executable: bool
    scope_is_complete: bool


class DelegationDecision(FrozenModel):
    """Typed routing outcome with a stable machine-readable reason."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    target: DelegationTarget
    reason: NonEmpty


class WorkerScope(FrozenModel):
    """Exact resources and operations visible to one worker request."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    resources: tuple[NonEmpty, ...] = Field(min_length=1)
    operations: tuple[NonEmpty, ...] = Field(min_length=1)


class WorkerBudget(FrozenModel):
    """Bounded retry, token, and cost allowance for one worker request."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    max_attempts: int = Field(ge=1, le=5)
    max_tokens: int = Field(ge=1)
    max_cost_microunits: int = Field(ge=0)


class WorkerTimeout(FrozenModel):
    """Hard runtime time allowance in seconds."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    seconds: int = Field(ge=1, le=3600)


class WorkerRequestProvenance(FrozenModel):
    """Canonical origin and audit attribution for a worker request."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    source: NonEmpty
    source_id: NonEmpty
    actor: Annotated[ActorId, StringConstraints(min_length=1)]
    correlation_id: Annotated[CorrelationId, StringConstraints(min_length=1)]


class WorkerRequest(FrozenModel):
    """Fully scoped request accepted by a fabric runtime."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    request_id: UUID
    worker_id: NonEmpty
    capability: WorkerCapability
    objective: NonEmpty
    scope: WorkerScope
    budget: WorkerBudget
    timeout: WorkerTimeout
    provenance: WorkerRequestProvenance
    credential_references: tuple[NonEmpty, ...] = ()

    def canonical_payload(self) -> str:
        """Return deterministic non-secret JSON for proposal payload binding."""
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    def authority_action_class(self) -> str:
        """Map a capability to the canonical Todo 3 approval action class."""
        match self.capability:  # noqa: MATCH_OK - enum is already exhaustive
            case (
                WorkerCapability.CODE
                | WorkerCapability.BROWSER
                | WorkerCapability.COMPUTER
                | WorkerCapability.DOCUMENT
            ):
                return "worker.execute"
            case WorkerCapability.EXTERNAL_SEND:
                return "message.send"


class CredentialGrant(FrozenModel):
    """Short-lived opaque broker handle, never raw credential material."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    reference: NonEmpty
    handle: NonEmpty
    expires_at: datetime
    request_id: UUID | None = None
    capability: WorkerCapability | None = None
    issued_at: datetime | None = None


class RuntimeDescriptor(FrozenModel):
    """Replaceable Integration Fabric runtime identity and version."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    name: NonEmpty
    version: NonEmpty
    execution_class: WorkerRuntimeClass


class WorkerAttemptResult(FrozenModel):
    """Result returned by one bounded runtime attempt."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    status: WorkerAttemptStatus
    detail: NonEmpty
    output_reference: str | None = None

    @model_validator(mode="after")
    def require_output_for_success(self) -> Self:
        """Ensure successful attempts identify their externalized artifact."""
        has_output = self.output_reference is not None and bool(self.output_reference)
        if self.status == WorkerAttemptStatus.SUCCEEDED and not has_output:
            message = "successful worker attempt requires an output reference"
            raise ValueError(message)
        return self

    @classmethod
    def succeeded(cls, output_reference: str) -> "WorkerAttemptResult":
        """Build a successful runtime attempt."""
        return cls(
            status=WorkerAttemptStatus.SUCCEEDED,
            detail="completed",
            output_reference=output_reference,
        )

    @classmethod
    def failed(cls, reason: str) -> "WorkerAttemptResult":
        """Build a terminal runtime failure."""
        return cls(status=WorkerAttemptStatus.FAILED, detail=reason)

    @classmethod
    def retry(cls, reason: str) -> "WorkerAttemptResult":
        """Build an explicitly retryable runtime result."""
        return cls(status=WorkerAttemptStatus.RETRY, detail=reason)


class WorkerResultProvenance(FrozenModel):
    """Runtime and request identities attached to every Commander result."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    request_id: UUID
    runtime: RuntimeDescriptor


class WorkerResult(FrozenModel):
    """Final typed result after policy checks and bounded retries."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    status: WorkerResultStatus
    detail: NonEmpty
    attempts: int = Field(ge=0)
    provenance: WorkerResultProvenance
    output_reference: str | None = None


class DispatchAuthorization(FrozenModel):
    """Approved proposal and exact capability lease for protected dispatch."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    proposal: ProposalRecord
    lease: CapabilityLease


class CredentialBroker(Protocol):
    """Issue only opaque, short-lived credential handles for one request."""

    def issue(self, request: WorkerRequest) -> tuple[CredentialGrant, ...]:
        """Return brokered handles scoped to the request."""
        ...


class WorkerRuntime(Protocol):
    """Replaceable fabric runtime contract; Codex is one possible implementation."""

    descriptor: RuntimeDescriptor

    def execute(
        self,
        request: WorkerRequest,
        credentials: tuple[CredentialGrant, ...],
    ) -> WorkerAttemptResult:
        """Execute one bounded attempt and return a typed outcome."""
        ...
