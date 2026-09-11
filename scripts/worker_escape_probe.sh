#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

uv run python - <<'PY'
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from secretary_service.commander import (
    WorkerBudget,
    WorkerCapability,
    WorkerRequest,
    WorkerRequestProvenance,
    WorkerScope,
    WorkerTimeout,
)
from secretary_service.models import ActorId, CorrelationId
from secretary_service.workers import (
    SandboxLimits,
    SandboxWorker,
    ScopedCredentialIssuer,
    WorkerCancellationRegistry,
)
from tests.helpers import FakeClock


def make_request(
    capability: WorkerCapability,
    resource: str,
    operation: str,
    credential_references: tuple[str, ...] = (),
) -> WorkerRequest:
    return WorkerRequest(
        request_id=uuid4(),
        worker_id="escape-probe",
        capability=capability,
        objective="prove the worker boundary fails closed",
        scope=WorkerScope(resources=(resource,), operations=(operation,)),
        budget=WorkerBudget(max_attempts=1, max_tokens=50, max_cost_microunits=0),
        timeout=WorkerTimeout(seconds=5),
        provenance=WorkerRequestProvenance(
            source="verification",
            source_id="task-17",
            actor=ActorId("probe"),
            correlation_id=CorrelationId("worker-escape-probe"),
        ),
        credential_references=credential_references,
    )


with TemporaryDirectory(prefix="lifeos-worker-probe-") as directory:
    root = Path(directory)
    allowed = root / "allowed"
    allowed.mkdir()
    outside = root / "outside"
    outside.mkdir()
    (allowed / "link").symlink_to(outside, target_is_directory=True)
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    cancellations = WorkerCancellationRegistry()
    credentials = ScopedCredentialIssuer(
        clock,
        b"worker-escape-probe-key",
        timedelta(seconds=20),
    )
    worker = SandboxWorker(
        clock,
        SandboxLimits(
            filesystem_roots=(allowed,),
            allowed_sites=("calendar.example.test",),
            max_timeout_seconds=5,
            max_tokens=50,
            max_cost_microunits=0,
            credential_ttl=timedelta(seconds=30),
        ),
        credential_issuer=credentials,
        cancellation=cancellations,
    )

    probes = {
        "filesystem_escape": worker.execute(
            make_request(WorkerCapability.DOCUMENT, str(allowed / "link" / "escape"), "write"),
            (),
        ).detail,
        "site_escape": worker.execute(
            make_request(WorkerCapability.BROWSER, "https://calendar.example.test.evil/", "read"),
            (),
        ).detail,
        "computer": worker.execute(
            make_request(WorkerCapability.COMPUTER, "screen:main", "click"),
            (),
        ).detail,
        "send": worker.execute(
            make_request(WorkerCapability.EXTERNAL_SEND, "message:test", "send"),
            (),
        ).detail,
    }
    credential_request = make_request(
        WorkerCapability.BROWSER,
        "https://calendar.example.test/day",
        "read",
        ("calendar:read",),
    )
    grant = credentials.issue(credential_request)[0]
    probes["forged_credential"] = worker.execute(
        credential_request,
        (grant.model_copy(update={"handle": "forged"}),),
    ).detail
    accepted = worker.execute(credential_request, (grant,))
    probes["credential_replay"] = worker.execute(credential_request, (grant,)).detail
    cancelled_request = make_request(
        WorkerCapability.DOCUMENT,
        str(allowed / "cancelled"),
        "write",
    )
    cancellations.cancel(cancelled_request.request_id)
    probes["cancel"] = worker.execute(cancelled_request, ()).detail

    expected = {
        "filesystem_escape": "filesystem_not_allowed",
        "site_escape": "site_not_allowed",
        "computer": "capability_disabled",
        "send": "capability_disabled",
        "forged_credential": "credential_forged",
        "credential_replay": "credential_replay",
        "cancel": "cancelled",
    }
    assert accepted.output_reference is not None
    assert probes == expected
    assert worker.external_operations == 0
    assert not (outside / "escape").exists()
    for name, result in probes.items():
        print(f"{name}: denied ({result})")
    print("external_operations: 0")
PY
