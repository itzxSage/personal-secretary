"""Contract-gated health API for the dry-run foundation service."""

from typing import Annotated, ClassVar, Literal

from fastapi import FastAPI, Header, status
from fastapi.responses import JSONResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    RootModel,
    StringConstraints,
    ValidationError,
)
from starlette.responses import Response

from secretary_service.contract_version import CONTRACT_VERSION

type SemanticVersion = Annotated[str, StringConstraints(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]


class ClientContractVersion(RootModel[SemanticVersion]):
    """Parsed contract version received at the HTTP boundary."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)


class HealthStatus(BaseModel):
    """No-connectors service readiness response."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    status: Literal["ok"]
    contract_version: str
    connectors: Literal["disabled"]
    dry_run: Literal[True]


class ContractErrorDetail(BaseModel):
    """Machine-readable incompatible-contract details."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    code: Literal["contract_version_mismatch"]
    expected_version: str
    received_version: str


class ContractErrorResponse(BaseModel):
    """Envelope for contract compatibility failures."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    error: ContractErrorDetail


def incompatible_contract_response(received_version: str) -> JSONResponse:
    """Build the typed upgrade-required response."""
    payload = ContractErrorResponse(
        error=ContractErrorDetail(
            code="contract_version_mismatch",
            expected_version=CONTRACT_VERSION,
            received_version=received_version,
        )
    )
    return JSONResponse(
        status_code=status.HTTP_426_UPGRADE_REQUIRED,
        content=payload.model_dump(mode="json"),
    )


app = FastAPI(title="Secretary Service")


@app.get(
    "/health",
    response_model=HealthStatus,
    responses={status.HTTP_426_UPGRADE_REQUIRED: {"model": ContractErrorResponse}},
)
def health(
    contract_version: Annotated[
        str | None,
        Header(alias="X-Secretary-Contract-Version"),
    ] = None,
) -> Response:
    """Report dry-run readiness only to contract-compatible clients."""
    received_version = contract_version or ""
    try:
        _ = ClientContractVersion.model_validate(received_version)
    except ValidationError:
        return incompatible_contract_response(received_version)

    if received_version != CONTRACT_VERSION:
        return incompatible_contract_response(received_version)

    payload = HealthStatus(
        status="ok",
        contract_version=CONTRACT_VERSION,
        connectors="disabled",
        dry_run=True,
    )
    return JSONResponse(content=payload.model_dump(mode="json"))
