"""Inert no-op and allowlisted sandbox worker runtimes."""

import hashlib
from pathlib import Path
from typing import final
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from secretary_service.commander import (
    CredentialGrant,
    RuntimeDescriptor,
    WorkerAttemptResult,
    WorkerCapability,
    WorkerRequest,
    WorkerRuntimeClass,
)
from secretary_service.models import RecordId
from secretary_service.storage import Clock
from secretary_service.workers.credentials import CredentialGateError, ScopedCredentialIssuer
from secretary_service.workers.models import (
    ArtifactProvenance,
    EffectStatus,
    SandboxLimits,
    WorkerEffect,
    WorkerGateStatus,
)


@final
class WorkerCancellationRegistry:
    """Mutable cancellation boundary checked before sandbox effects commit."""

    def __init__(self) -> None:
        """Start with no cancelled requests."""
        self._cancelled: set[UUID] = set()

    def cancel(self, request_id: UUID) -> None:
        """Cancel one request idempotently."""
        self._cancelled.add(request_id)

    def is_cancelled(self, request_id: UUID) -> bool:
        """Return whether cancellation was requested."""
        return request_id in self._cancelled


@final
class NoOpWorker:
    """Default worker runtime that cannot perform any operation."""

    descriptor = RuntimeDescriptor(
        name="lifeos-noop",
        version="1",
        execution_class=WorkerRuntimeClass.NOOP,
    )

    @property
    def external_operations(self) -> int:
        """Prove the no-op worker has no external operation surface."""
        return 0

    def execute(
        self,
        request: WorkerRequest,
        credentials: tuple[CredentialGrant, ...],
    ) -> WorkerAttemptResult:
        """Reject every request without inspecting credentials or targets."""
        _ = request, credentials
        return WorkerAttemptResult.failed("noop_worker")


@final
class SandboxWorker:
    """Simulate allowlisted browser/document work without external I/O."""

    descriptor = RuntimeDescriptor(
        name="lifeos-sandbox",
        version="1",
        execution_class=WorkerRuntimeClass.SANDBOX,
    )

    def __init__(
        self,
        clock: Clock,
        limits: SandboxLimits,
        credential_issuer: ScopedCredentialIssuer | None = None,
        cancellation: WorkerCancellationRegistry | None = None,
    ) -> None:
        """Configure an inert runtime with closed limits and optional gates."""
        self._clock = clock
        self._limits = limits
        self._credential_issuer = credential_issuer
        self._cancellation = cancellation or WorkerCancellationRegistry()
        self._artifacts: list[ArtifactProvenance] = []
        self._effects: dict[RecordId, WorkerEffect] = {}

    @property
    def external_operations(self) -> int:
        """Prove this runtime never invokes network, computer, or host filesystem I/O."""
        return 0

    @property
    def artifacts(self) -> tuple[ArtifactProvenance, ...]:
        """Return immutable artifact provenance in creation order."""
        return tuple(self._artifacts)

    @property
    def effects(self) -> tuple[WorkerEffect, ...]:
        """Return reversible effect evidence in creation order."""
        return tuple(self._effects.values())

    def execute(
        self,
        request: WorkerRequest,
        credentials: tuple[CredentialGrant, ...],
    ) -> WorkerAttemptResult:
        """Validate all gates, then create only an in-memory sandbox artifact."""
        started_at = self._clock.now()
        denial = self._request_denial(request)
        if denial is not None:
            return WorkerAttemptResult.failed(denial)
        if self._cancellation.is_cancelled(request.request_id):
            return WorkerAttemptResult.failed("cancelled")
        credential_denial = self._credential_denial(request, credentials)
        if credential_denial is not None:
            return WorkerAttemptResult.failed(credential_denial)
        deadline = started_at.timestamp() + request.timeout.seconds
        if self._clock.now().timestamp() >= deadline:
            return WorkerAttemptResult.failed("timeout")
        if self._cancellation.is_cancelled(request.request_id):
            return WorkerAttemptResult.failed("cancelled")
        digest = hashlib.sha256(request.canonical_payload().encode()).hexdigest()
        reference = f"sandbox:{digest}"
        artifact = ArtifactProvenance(
            reference=reference,
            request_id=request.request_id,
            worker_id=request.worker_id,
            capability=request.capability,
            source=request.provenance.source,
            source_id=request.provenance.source_id,
            correlation_id=request.provenance.correlation_id,
            created_at=self._clock.now(),
            payload_sha256=digest,
            runtime=self.descriptor,
        )
        effect = WorkerEffect(
            effect_id=RecordId(uuid4()),
            request_id=request.request_id,
            artifact_reference=reference,
            status=EffectStatus.APPLIED,
            reversible=True,
            updated_at=self._clock.now(),
        )
        self._artifacts.append(artifact)
        self._effects[effect.effect_id] = effect
        return WorkerAttemptResult.succeeded(reference)

    def compensate(self, effect_id: RecordId) -> WorkerEffect:
        """Record deterministic compensation for an in-sandbox effect."""
        effect = self._effects[effect_id]
        compensated = effect.model_copy(
            update={"status": EffectStatus.COMPENSATED, "updated_at": self._clock.now()}
        )
        self._effects[effect_id] = compensated
        return compensated

    def reconcile(self, effect_id: RecordId) -> WorkerEffect:
        """Reconcile retained provenance after apply or compensation."""
        effect = self._effects[effect_id]
        matched = any(item.reference == effect.artifact_reference for item in self._artifacts)
        reconciled = effect.model_copy(
            update={
                "status": EffectStatus.RECONCILED,
                "reconciliation": (
                    WorkerGateStatus.MATCHED if matched else WorkerGateStatus.MISSING
                ),
                "updated_at": self._clock.now(),
            }
        )
        self._effects[effect_id] = reconciled
        return reconciled

    def _request_denial(self, request: WorkerRequest) -> str | None:
        if request.timeout.seconds > self._limits.max_timeout_seconds:
            return "timeout_exceeded"
        budget = request.budget
        if (
            budget.max_attempts > self._limits.max_attempts
            or budget.max_tokens > self._limits.max_tokens
            or budget.max_cost_microunits > self._limits.max_cost_microunits
        ):
            return "budget_exceeded"
        match request.capability:  # noqa: MATCH_OK - enum is already exhaustive
            case WorkerCapability.BROWSER:
                return self._browser_denial(request)
            case WorkerCapability.DOCUMENT:
                return self._document_denial(request)
            case WorkerCapability.CODE | WorkerCapability.COMPUTER | WorkerCapability.EXTERNAL_SEND:
                return "capability_disabled"

    def _browser_denial(self, request: WorkerRequest) -> str | None:
        if any(operation not in {"navigate", "read"} for operation in request.scope.operations):
            return "operation_not_allowed"
        for resource in request.scope.resources:
            parsed = urlsplit(resource)
            try:
                port = parsed.port
            except ValueError:
                return "site_not_allowed"
            checks = (
                (parsed.scheme != "https", "https_required"),
                (parsed.username is not None or parsed.password is not None, "site_not_allowed"),
                (parsed.hostname not in self._limits.allowed_sites, "site_not_allowed"),
                (port not in {None, 443}, "site_not_allowed"),
            )
            denial = next((reason for failed, reason in checks if failed), None)
            if denial is not None:
                return denial
        return None

    def _document_denial(self, request: WorkerRequest) -> str | None:
        if any(operation not in {"read", "write"} for operation in request.scope.operations):
            return "operation_not_allowed"
        roots = tuple(root.resolve() for root in self._limits.filesystem_roots)
        for resource in request.scope.resources:
            resolved = Path(resource).resolve()
            if not any(resolved.is_relative_to(root) for root in roots):
                return "filesystem_not_allowed"
        return None

    def _credential_denial(
        self,
        request: WorkerRequest,
        credentials: tuple[CredentialGrant, ...],
    ) -> str | None:
        if not request.credential_references and not credentials:
            return None
        if self._credential_issuer is None:
            return "credential_verifier_unavailable"
        try:
            self._credential_issuer.consume(request, credentials, self._limits.credential_ttl)
        except CredentialGateError as error:
            return str(error)
        return None
