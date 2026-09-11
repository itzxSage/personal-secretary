"""Isolation and lifecycle tests for inert worker runtimes."""

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import final
from uuid import uuid4

import pytest

from secretary_service.commander import (
    Commander,
    CredentialGrant,
    RuntimeDescriptor,
    WorkerAttemptResult,
    WorkerBudget,
    WorkerCapability,
    WorkerRequest,
    WorkerRequestProvenance,
    WorkerRuntimeClass,
    WorkerScope,
    WorkerTimeout,
)
from secretary_service.models import ActorId, CorrelationId
from secretary_service.workers import (
    EffectStatus,
    NoOpWorker,
    SandboxLimits,
    SandboxWorker,
    ScopedCredentialIssuer,
    WorkerCancellationRegistry,
    WorkerGateStatus,
)
from tests.helpers import FakeClock


@dataclass(frozen=True, slots=True)
class EscapeProbe:
    capability: WorkerCapability
    resource: str
    operation: str
    reason: str


@final
class LiveRuntime:
    descriptor = RuntimeDescriptor(
        name="live-test",
        version="1",
        execution_class=WorkerRuntimeClass.LIVE,
    )

    def __init__(self) -> None:
        self.calls = 0

    def execute(
        self,
        request: WorkerRequest,
        credentials: tuple[CredentialGrant, ...],
    ) -> WorkerAttemptResult:
        _ = request, credentials
        self.calls += 1
        return WorkerAttemptResult.succeeded("unexpected")


def request(
    capability: WorkerCapability,
    resource: str,
    operation: str,
    *,
    credential_references: tuple[str, ...] = (),
) -> WorkerRequest:
    return WorkerRequest(
        request_id=uuid4(),
        worker_id="sandbox-worker",
        capability=capability,
        objective="produce a bounded test artifact",
        scope=WorkerScope(resources=(resource,), operations=(operation,)),
        budget=WorkerBudget(max_attempts=1, max_tokens=100, max_cost_microunits=0),
        timeout=WorkerTimeout(seconds=5),
        provenance=WorkerRequestProvenance(
            source="test",
            source_id="task-17",
            actor=ActorId("user"),
            correlation_id=CorrelationId("task-17-probe"),
        ),
        credential_references=credential_references,
    )


def limits(tmp_path: Path) -> SandboxLimits:
    return SandboxLimits(
        filesystem_roots=(tmp_path / "allowed",),
        allowed_sites=("calendar.example.test",),
        max_timeout_seconds=10,
        max_tokens=500,
        max_cost_microunits=0,
        credential_ttl=timedelta(seconds=30),
    )


def test_noop_worker_never_performs_an_operation(tmp_path: Path) -> None:
    worker = NoOpWorker()

    outcome = worker.execute(
        request(WorkerCapability.DOCUMENT, str(tmp_path / "allowed" / "note.txt"), "write"),
        (),
    )

    assert outcome.detail == "noop_worker"
    assert worker.external_operations == 0


def test_live_browser_runtime_requires_explicit_capability_promotion() -> None:
    runtime = LiveRuntime()

    outcome = Commander(runtime=runtime).dispatch(
        request(WorkerCapability.BROWSER, "https://calendar.example.test/", "read")
    )

    assert outcome.detail == "live_capability_not_promoted"
    assert runtime.calls == 0


@pytest.mark.parametrize(
    "probe",
    [
        EscapeProbe(
            WorkerCapability.BROWSER,
            "https://evil.example/",
            "navigate",
            "site_not_allowed",
        ),
        EscapeProbe(
            WorkerCapability.BROWSER,
            "https://calendar.example.test.evil/",
            "read",
            "site_not_allowed",
        ),
        EscapeProbe(
            WorkerCapability.BROWSER,
            "http://calendar.example.test/",
            "read",
            "https_required",
        ),
        EscapeProbe(
            WorkerCapability.DOCUMENT,
            "../escape.txt",
            "write",
            "filesystem_not_allowed",
        ),
        EscapeProbe(
            WorkerCapability.COMPUTER,
            "screen:main",
            "click",
            "capability_disabled",
        ),
        EscapeProbe(
            WorkerCapability.EXTERNAL_SEND,
            "message:test",
            "send",
            "capability_disabled",
        ),
    ],
)
def test_escape_and_live_action_attempts_fail_before_any_operation(
    tmp_path: Path,
    clock: FakeClock,
    probe: EscapeProbe,
) -> None:
    worker = SandboxWorker(clock, limits(tmp_path))

    outcome = worker.execute(
        request(probe.capability, probe.resource, probe.operation),
        (),
    )

    assert outcome.detail == probe.reason
    assert worker.external_operations == 0
    assert worker.artifacts == ()


def test_symlink_escape_is_rejected(tmp_path: Path, clock: FakeClock) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (allowed / "link").symlink_to(outside, target_is_directory=True)
    worker = SandboxWorker(clock, limits(tmp_path))

    outcome = worker.execute(
        request(WorkerCapability.DOCUMENT, str(allowed / "link" / "escape.txt"), "write"),
        (),
    )

    assert outcome.detail == "filesystem_not_allowed"
    assert not (outside / "escape.txt").exists()


def test_scoped_credentials_reject_forgery_expiry_and_replay(
    tmp_path: Path,
    clock: FakeClock,
) -> None:
    credential_issuer = ScopedCredentialIssuer(
        clock=clock,
        signing_key=b"task-17-credential-key",
        ttl=timedelta(seconds=20),
    )
    worker = SandboxWorker(clock, limits(tmp_path), credential_issuer=credential_issuer)
    work = request(
        WorkerCapability.BROWSER,
        "https://calendar.example.test/day",
        "read",
        credential_references=("calendar:read",),
    )
    grant = credential_issuer.issue(work)[0]
    forged = grant.model_copy(update={"handle": f"{grant.handle}forged"})

    forged_outcome = worker.execute(work, (forged,))
    accepted = worker.execute(work, (grant,))
    replayed = worker.execute(work, (grant,))

    assert forged_outcome.detail == "credential_forged"
    assert accepted.output_reference is not None
    assert replayed.detail == "credential_replay"

    fresh_work = work.model_copy(update={"request_id": uuid4()})
    expired = credential_issuer.issue(fresh_work)
    clock.advance(timedelta(seconds=21))
    expired_outcome = worker.execute(fresh_work, expired)
    assert expired_outcome.detail == "credential_expired"


def test_cancel_and_budget_limits_fail_closed(tmp_path: Path, clock: FakeClock) -> None:
    cancellation = WorkerCancellationRegistry()
    worker = SandboxWorker(clock, limits(tmp_path), cancellation=cancellation)
    work = request(
        WorkerCapability.DOCUMENT,
        str(tmp_path / "allowed" / "note.txt"),
        "write",
    )
    cancellation.cancel(work.request_id)

    cancelled = worker.execute(work, ())
    over_budget = worker.execute(
        work.model_copy(
            update={
                "request_id": uuid4(),
                "budget": WorkerBudget(
                    max_attempts=1,
                    max_tokens=501,
                    max_cost_microunits=0,
                ),
            }
        ),
        (),
    )
    over_timeout = worker.execute(
        work.model_copy(update={"request_id": uuid4(), "timeout": WorkerTimeout(seconds=11)}),
        (),
    )

    assert cancelled.detail == "cancelled"
    assert over_budget.detail == "budget_exceeded"
    assert over_timeout.detail == "timeout_exceeded"
    assert worker.artifacts == ()


def test_sandbox_artifact_records_provenance_compensation_and_reconciliation(
    tmp_path: Path,
    clock: FakeClock,
) -> None:
    worker = SandboxWorker(clock, limits(tmp_path))
    work = request(
        WorkerCapability.DOCUMENT,
        str(tmp_path / "allowed" / "note.txt"),
        "write",
    )

    outcome = worker.execute(work, ())
    artifact = worker.artifacts[0]
    applied_effect = worker.effects[0]
    compensated = worker.compensate(applied_effect.effect_id)
    reconciled = worker.reconcile(applied_effect.effect_id)

    assert outcome.output_reference == artifact.reference
    assert artifact.request_id == work.request_id
    assert artifact.payload_sha256
    assert artifact.runtime == worker.descriptor
    assert applied_effect.status == EffectStatus.APPLIED
    assert compensated.status == EffectStatus.COMPENSATED
    assert reconciled.status == EffectStatus.RECONCILED
    assert reconciled.reconciliation == WorkerGateStatus.MATCHED
    assert worker.external_operations == 0
