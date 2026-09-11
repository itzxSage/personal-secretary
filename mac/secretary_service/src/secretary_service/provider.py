"""Provider contract and deterministic, network-free fixture implementation."""

from dataclasses import dataclass
from typing import ClassVar, Protocol, final, override

from pydantic import ConfigDict

from secretary_service.models import FrozenModel, NonEmpty


class ProviderRequest(FrozenModel):
    """Content-minimized request addressed by schema and fingerprint."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    schema_name: NonEmpty
    input_fingerprint: NonEmpty


class ProviderResponse(FrozenModel):
    """Schema-bound provider result."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    schema_name: NonEmpty
    output_json: NonEmpty
    provider_version: NonEmpty


class Provider(Protocol):
    """Provider seam implemented by fakes now and real adapters later."""

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        """Generate one schema-compatible response."""
        ...


class ProviderFixture(FrozenModel):
    """One exact request-response fixture."""

    request: ProviderRequest
    response: ProviderResponse


@final
class ProviderContractError(Exception):
    """Unknown request rejected by the deterministic fake."""

    def __init__(self, schema_name: str, input_fingerprint: str) -> None:
        super().__init__(schema_name, input_fingerprint)
        self.schema_name = schema_name
        self.input_fingerprint = input_fingerprint

    @override
    def __str__(self) -> str:
        return f"no deterministic fixture for schema {self.schema_name}"


@dataclass(frozen=True, slots=True)
class DeterministicFakeProvider:
    """Network-free provider returning only exact registered fixtures."""

    fixtures: tuple[ProviderFixture, ...]

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        """Return the exact fixture response or fail closed."""
        for fixture in self.fixtures:
            if fixture.request == request:
                return fixture.response
        raise ProviderContractError(
            schema_name=request.schema_name,
            input_fingerprint=request.input_fingerprint,
        )
