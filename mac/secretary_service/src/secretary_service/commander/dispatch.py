"""Lease-constrained dispatch to a replaceable Integration Fabric runtime."""

from typing import Final, final

from secretary_service.authority import ProposalState
from secretary_service.commander.contracts import (
    CredentialBroker,
    DispatchAuthorization,
    RuntimeDescriptor,
    WorkerAttemptStatus,
    WorkerCapability,
    WorkerRequest,
    WorkerResult,
    WorkerResultProvenance,
    WorkerResultStatus,
    WorkerRuntime,
    WorkerRuntimeClass,
)
from secretary_service.commander.credentials import CredentialUnavailableError, NoCredentialBroker
from secretary_service.leases import LeaseIssuer, LeaseViolationError

NO_WORKER_RUNTIME: Final = RuntimeDescriptor(
    name="none",
    version="none",
    execution_class=WorkerRuntimeClass.NOOP,
)
NO_PROMOTED_CAPABILITIES: Final[frozenset[WorkerCapability]] = frozenset()
PROTECTED_CAPABILITIES: Final = frozenset(
    {
        WorkerCapability.BROWSER,
        WorkerCapability.COMPUTER,
        WorkerCapability.DOCUMENT,
        WorkerCapability.EXTERNAL_SEND,
    }
)


@final
class Commander:
    """Route bounded worker requests only after exact capability authorization."""

    def __init__(
        self,
        runtime: WorkerRuntime | None = None,
        credential_broker: CredentialBroker | None = None,
        lease_issuer: LeaseIssuer | None = None,
        promoted_capabilities: frozenset[WorkerCapability] = NO_PROMOTED_CAPABILITIES,
    ) -> None:
        """Use fail-closed defaults for absent runtime, broker, or lease verifier."""
        self._runtime = runtime
        self._credential_broker = credential_broker or NoCredentialBroker()
        self._lease_issuer = lease_issuer
        self._promoted_capabilities = promoted_capabilities

    def dispatch(
        self,
        request: WorkerRequest,
        authorization: DispatchAuthorization | None = None,
    ) -> WorkerResult:
        """Enforce authorization, broker credentials, and handle bounded retries."""
        denial = self._authorization_denial(request, authorization)
        if denial is not None:
            return self._result(request, WorkerResultStatus.DENIED, denial, 0)
        if self._runtime is None:
            return self._result(request, WorkerResultStatus.UNAVAILABLE, "no_worker_configured", 0)
        try:
            credentials = self._credential_broker.issue(request)
        except CredentialUnavailableError as error:
            return self._result(request, WorkerResultStatus.DENIED, str(error), 0)

        for attempt in range(1, request.budget.max_attempts + 1):
            outcome = self._runtime.execute(request, credentials)
            match outcome.status:  # noqa: MATCH_OK - enum is already exhaustive
                case WorkerAttemptStatus.SUCCEEDED:
                    return self._result(
                        request,
                        WorkerResultStatus.SUCCEEDED,
                        outcome.detail,
                        attempt,
                        outcome.output_reference,
                    )
                case WorkerAttemptStatus.FAILED:
                    return self._result(
                        request,
                        WorkerResultStatus.FAILED,
                        outcome.detail,
                        attempt,
                    )
                case WorkerAttemptStatus.RETRY:
                    if attempt == request.budget.max_attempts:
                        return self._result(
                            request,
                            WorkerResultStatus.RETRY_EXHAUSTED,
                            outcome.detail,
                            attempt,
                        )
        raise AssertionError(request.request_id)

    def _authorization_denial(  # noqa: PLR0911 - ordered fail-closed boundary checks
        self,
        request: WorkerRequest,
        authorization: DispatchAuthorization | None,
    ) -> str | None:
        # A configured live coding runtime has host effects just like computer
        # control. Sandbox test runtimes do not establish permission to launch it.
        if (
            request.capability is WorkerCapability.CODE
            and self._runtime is not None
            and self._runtime.descriptor.execution_class is WorkerRuntimeClass.LIVE
        ):
            return "live_code_execution_disabled"
        if request.capability not in PROTECTED_CAPABILITIES:
            return None
        live_control = request.capability in {
            WorkerCapability.BROWSER,
            WorkerCapability.COMPUTER,
            WorkerCapability.DOCUMENT,
        }
        if (
            live_control
            and self._runtime is not None
            and self._runtime.descriptor.execution_class is WorkerRuntimeClass.LIVE
            and request.capability not in self._promoted_capabilities
        ):
            return "live_capability_not_promoted"
        if authorization is None or self._lease_issuer is None:
            return (
                "capability_lease_required"
                if authorization is None
                else "capability_lease_verifier_unavailable"
            )
        proposal = authorization.proposal
        lease = authorization.lease
        checks = (
            (proposal.state != ProposalState.APPROVED, "approved_proposal_required"),
            (
                proposal.action_class != request.authority_action_class(),
                "proposal_action_class_mismatch",
            ),
            (proposal.payload != request.canonical_payload(), "proposal_payload_mismatch"),
            (lease.capability != request.capability.value, "lease_capability_mismatch"),
            (lease.worker_id != request.worker_id, "lease_worker_mismatch"),
        )
        denial = next((reason for failed, reason in checks if failed), None)
        if denial is not None:
            return denial
        try:
            _ = self._lease_issuer.verify(lease, proposal)
        except LeaseViolationError as error:
            return str(error)
        return None

    def _result(
        self,
        request: WorkerRequest,
        status: WorkerResultStatus,
        detail: str,
        attempts: int,
        output_reference: str | None = None,
    ) -> WorkerResult:
        runtime = self._runtime.descriptor if self._runtime is not None else NO_WORKER_RUNTIME
        return WorkerResult(
            status=status,
            detail=detail,
            attempts=attempts,
            output_reference=output_reference,
            provenance=WorkerResultProvenance(request_id=request.request_id, runtime=runtime),
        )
