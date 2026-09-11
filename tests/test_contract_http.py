from fastapi.testclient import TestClient

from secretary_service.app import ContractErrorDetail, ContractErrorResponse, HealthStatus, app
from secretary_service.contract_version import CONTRACT_VERSION


def test_health_succeeds_when_contract_version_matches() -> None:
    # Given: a client using the service contract version.
    client = TestClient(app)

    # When: the client checks service health.
    response = client.get(
        "/health",
        headers={"X-Secretary-Contract-Version": CONTRACT_VERSION},
    )

    # Then: the service reports a compatible dry-run state.
    assert response.status_code == 200
    assert HealthStatus.model_validate_json(response.content) == HealthStatus(
        status="ok",
        contract_version=CONTRACT_VERSION,
        connectors="disabled",
        dry_run=True,
    )


def test_health_rejects_mismatched_contract_with_typed_upgrade_error() -> None:
    # Given: a client contract version that cannot equal the service version.
    client = TestClient(app)
    mismatched_version = "999.0.0"

    # When: the incompatible client checks service health.
    response = client.get(
        "/health",
        headers={"X-Secretary-Contract-Version": mismatched_version},
    )

    # Then: the response is a typed upgrade-required error.
    assert response.status_code == 426
    assert ContractErrorResponse.model_validate_json(response.content) == ContractErrorResponse(
        error=ContractErrorDetail(
            code="contract_version_mismatch",
            expected_version=CONTRACT_VERSION,
            received_version=mismatched_version,
        )
    )


def test_health_rejects_malformed_contract_version() -> None:
    # Given: a malformed client contract header.
    client = TestClient(app)

    # When: the malformed version reaches the HTTP boundary.
    response = client.get(
        "/health",
        headers={"X-Secretary-Contract-Version": "not-a-version"},
    )

    # Then: it fails closed as a typed compatibility error.
    assert response.status_code == 426
    assert ContractErrorResponse.model_validate_json(response.content) == ContractErrorResponse(
        error=ContractErrorDetail(
            code="contract_version_mismatch",
            expected_version=CONTRACT_VERSION,
            received_version="not-a-version",
        )
    )


def test_health_rejects_missing_contract_version() -> None:
    # Given: a client that does not advertise a contract version.
    client = TestClient(app)

    # When: the unversioned client checks service health.
    response = client.get("/health")

    # Then: it receives the same typed compatibility boundary response.
    assert response.status_code == 426
    assert ContractErrorResponse.model_validate_json(response.content) == ContractErrorResponse(
        error=ContractErrorDetail(
            code="contract_version_mismatch",
            expected_version=CONTRACT_VERSION,
            received_version="",
        )
    )
