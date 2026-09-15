"""Commander delegation routing and constrained fabric dispatch."""

from datetime import timedelta
from typing import final
from uuid import uuid4

import pytest

from secretary_service.authority import ProposalRecord, ProposalState
from secretary_service.commander import (
    Commander,
    CredentialGrant,
    DelegationCase,
    DelegationTarget,
    DispatchAuthorization,
    RuntimeDescriptor,
    WorkerAttemptResult,
    WorkerBudget,
    WorkerCapability,
    WorkerRequest,
    WorkerRequestProvenance,
    WorkerResultStatus,
    WorkerRuntimeClass,
    WorkerScope,
    WorkerTimeout,
    route_delegation,
)
from secretary_service.leases import LeaseIssuer, LeaseRequest, SigningKeyRing
from secretary_service.models import ActorId, CorrelationId, RecordId
from tests.helpers import FakeClock

WORKER_ID = "fabric-worker-1"
ACTOR = ActorId("user")
CORRELATION = CorrelationId("corr-delegation")


@final
class FakeRuntime:
    """Deterministic replaceable fabric runtime used by dispatch tests."""

    descriptor = RuntimeDescriptor(
        name="codex",
        version="test-v1",
        execution_class=WorkerRuntimeClass.SANDBOX,
    )

    def __init__(self, outcomes: list[WorkerAttemptResult]) -> None:
        self._outcomes: list[WorkerAttemptResult] = outcomes
        self.requests: list[WorkerRequest] = []

    def execute(
        self, request: WorkerRequest, credentials: tuple[CredentialGrant, ...]
    ) -> WorkerAttemptResult:
        self.requests.append(request)
        assert all(grant.handle.startswith("broker:") for grant in credentials)
        return self._outcomes[len(self.requests) - 1]


@final
class FakeCredentialBroker:
    """Issues opaque handles without exposing credential material."""

    def __init__(self, clock: FakeClock) -> None:
        self._clock: FakeClock = clock
        self.requests: list[WorkerRequest] = []

    def issue(self, request: WorkerRequest) -> tuple[CredentialGrant, ...]:
        self.requests.append(request)
        return tuple(
            CredentialGrant(
                reference=reference,
                handle=f"broker:{reference}",
                expires_at=self._clock.now() + timedelta(minutes=2),
            )
            for reference in request.credential_references
        )


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        (
            DelegationCase(
                requires_jared_judgment=True,
                independently_executable=True,
                scope_is_complete=True,
            ),
            DelegationTarget.JARED,
        ),
        (
            DelegationCase(
                requires_jared_judgment=False,
                independently_executable=True,
                scope_is_complete=True,
            ),
            DelegationTarget.AGENT,
        ),
        (
            DelegationCase(
                requires_jared_judgment=False,
                independently_executable=False,
                scope_is_complete=True,
            ),
            DelegationTarget.ASSIST,
        ),
        (
            DelegationCase(
                requires_jared_judgment=False,
                independently_executable=True,
                scope_is_complete=False,
            ),
            DelegationTarget.ASSIST,
        ),
    ],
)
def test_commander_routes_deterministic_cases(
    case: DelegationCase,
    expected: DelegationTarget,
) -> None:
    decision = route_delegation(case)

    assert decision.target == expected


def make_request(capability: WorkerCapability) -> WorkerRequest:
    return WorkerRequest(
        request_id=uuid4(),
        worker_id=WORKER_ID,
        capability=capability,
        objective="complete the bounded task",
        scope=WorkerScope(resources=("artifact:task-10",), operations=("read", "write")),
        budget=WorkerBudget(max_attempts=2, max_tokens=2_000, max_cost_microunits=50_000),
        timeout=WorkerTimeout(seconds=30),
        provenance=WorkerRequestProvenance(
            source="conversation",
            source_id="turn-10",
            actor=ACTOR,
            correlation_id=CORRELATION,
        ),
        credential_references=("connector:test",),
    )


def authorize(
    clock: FakeClock,
    issuer: LeaseIssuer,
    request: WorkerRequest,
) -> DispatchAuthorization:
    proposal = ProposalRecord(
        proposal_id=RecordId(uuid4()),
        action_class=request.authority_action_class(),
        payload=request.canonical_payload(),
        state=ProposalState.APPROVED,
        created_at=clock.now(),
    )
    lease = issuer.issue(
        proposal,
        LeaseRequest(
            capability=request.capability.value,
            worker_id=request.worker_id,
            actor=ACTOR,
            correlation_id=CORRELATION,
            idempotency_key=str(request.request_id),
        ),
    )
    return DispatchAuthorization(proposal=proposal, lease=lease)


@pytest.mark.parametrize(
    "capability",
    [
        WorkerCapability.BROWSER,
        WorkerCapability.COMPUTER,
        WorkerCapability.EXTERNAL_SEND,
    ],
)
def test_protected_capability_without_lease_is_denied_before_runtime_or_credentials(
    clock: FakeClock,
    capability: WorkerCapability,
) -> None:
    runtime = FakeRuntime([WorkerAttemptResult.succeeded("artifact:unexpected")])
    broker = FakeCredentialBroker(clock)
    commander = Commander(runtime=runtime, credential_broker=broker)

    result = commander.dispatch(make_request(capability))

    assert result.status == WorkerResultStatus.DENIED
    assert result.attempts == 0
    assert runtime.requests == []
    assert broker.requests == []


def test_valid_exact_lease_allows_protected_dispatch_with_opaque_credentials(
    clock: FakeClock,
) -> None:
    issuer = LeaseIssuer(clock, SigningKeyRing(b"commander-signing-key"), timedelta(minutes=5))
    request = make_request(WorkerCapability.BROWSER)
    runtime = FakeRuntime([WorkerAttemptResult.succeeded("artifact:browser-result")])
    broker = FakeCredentialBroker(clock)
    commander = Commander(runtime=runtime, credential_broker=broker, lease_issuer=issuer)

    result = commander.dispatch(request, authorize(clock, issuer, request))

    assert result.status == WorkerResultStatus.SUCCEEDED
    assert result.output_reference == "artifact:browser-result"
    assert result.provenance.runtime == runtime.descriptor
    assert runtime.requests == [request]
    assert broker.requests == [request]


def test_lease_for_different_worker_is_denied_without_consuming_lease(
    clock: FakeClock,
) -> None:
    issuer = LeaseIssuer(clock, SigningKeyRing(b"commander-signing-key"), timedelta(minutes=5))
    request = make_request(WorkerCapability.EXTERNAL_SEND)
    authorization = authorize(clock, issuer, request)
    wrong_request = request.model_copy(update={"worker_id": "different-worker"})
    runtime = FakeRuntime([WorkerAttemptResult.succeeded("artifact:unexpected")])
    commander = Commander(
        runtime=runtime,
        credential_broker=FakeCredentialBroker(clock),
        lease_issuer=issuer,
    )

    result = commander.dispatch(wrong_request, authorization)

    assert result.status == WorkerResultStatus.DENIED
    assert runtime.requests == []

    accepted = commander.dispatch(request, authorization)
    assert accepted.status == WorkerResultStatus.SUCCEEDED
    assert runtime.requests == [request]


def test_default_credential_broker_denies_references_before_dispatch() -> None:
    runtime = FakeRuntime([WorkerAttemptResult.succeeded("artifact:unexpected")])
    commander = Commander(runtime=runtime)

    result = commander.dispatch(make_request(WorkerCapability.CODE))

    assert result.status == WorkerResultStatus.DENIED
    assert result.attempts == 0
    assert runtime.requests == []


def test_retryable_result_retries_within_budget_and_returns_attempt_count(clock: FakeClock) -> None:
    request = make_request(WorkerCapability.CODE)
    runtime = FakeRuntime(
        [
            WorkerAttemptResult.retry("runtime_busy"),
            WorkerAttemptResult.succeeded("artifact:code-result"),
        ]
    )
    commander = Commander(runtime=runtime, credential_broker=FakeCredentialBroker(clock))

    result = commander.dispatch(request)

    assert result.status == WorkerResultStatus.SUCCEEDED
    assert result.attempts == 2
    assert runtime.requests == [request, request]


def test_retryable_result_exhaustion_is_typed(clock: FakeClock) -> None:
    request = make_request(WorkerCapability.CODE)
    runtime = FakeRuntime(
        [
            WorkerAttemptResult.retry("runtime_busy"),
            WorkerAttemptResult.retry("runtime_busy"),
        ]
    )
    commander = Commander(runtime=runtime, credential_broker=FakeCredentialBroker(clock))

    result = commander.dispatch(request)

    assert result.status == WorkerResultStatus.RETRY_EXHAUSTED
    assert result.attempts == request.budget.max_attempts


def test_no_worker_is_the_default() -> None:
    result = Commander().dispatch(make_request(WorkerCapability.CODE))

    assert result.status == WorkerResultStatus.UNAVAILABLE
    assert result.attempts == 0
    assert result.provenance.runtime.name == "none"


def test_live_coding_runtime_cannot_inherit_sandbox_execution_permission(clock: FakeClock) -> None:
    runtime = FakeRuntime([WorkerAttemptResult.succeeded("artifact:unexpected")])
    runtime.descriptor = runtime.descriptor.model_copy(
        update={"execution_class": WorkerRuntimeClass.LIVE}
    )
    broker = FakeCredentialBroker(clock)
    commander = Commander(runtime=runtime, credential_broker=broker)

    result = commander.dispatch(make_request(WorkerCapability.CODE))

    assert result.status is WorkerResultStatus.DENIED
    assert result.detail == "live_code_execution_disabled"
    assert runtime.requests == []
    assert broker.requests == []
