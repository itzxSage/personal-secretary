from pathlib import Path
from typing import ClassVar
from uuid import UUID

import pytest
from pydantic import BaseModel, ConfigDict, Field

from secretary_service.export import StateExporter
from secretary_service.models import (
    ActorId,
    Connector,
    CorrelationId,
    EnergyCheckIn,
    RecordId,
    TransitionContext,
)
from secretary_service.provider import (
    DeterministicFakeProvider,
    ProviderContractError,
    ProviderFixture,
    ProviderRequest,
    ProviderResponse,
)
from secretary_service.storage import EncryptedStateStore
from tests.helpers import FakeClock


class ProviderSchema(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    required: list[str]


class Definitions(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    provider_request: ProviderSchema


class ContractSchema(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    definitions: Definitions = Field(alias="$defs")


def context(clock: FakeClock, correlation_id: str) -> TransitionContext:
    return TransitionContext(
        actor=ActorId("user"),
        correlation_id=CorrelationId(correlation_id),
        occurred_at=clock.now(),
    )


def test_user_requested_export_redacts_content_and_secret_references(
    store: EncryptedStateStore,
    clock: FakeClock,
) -> None:
    connector_reference = "keychain://connector/private-token"
    raw_health = "private-health-fixture"
    store.create(
        Connector(
            record_id=RecordId(UUID("00000000-0000-0000-0000-000000000201")),
            created_at=clock.now(),
            name="fixture-connector",
            secret_reference=connector_reference,
            state="disabled",
        ),
        context(clock, "corr-connector"),
    )
    store.create(
        EnergyCheckIn(
            record_id=RecordId(UUID("00000000-0000-0000-0000-000000000202")),
            created_at=clock.now(),
            energy=2,
            mood=raw_health,
        ),
        context(clock, "corr-health"),
    )

    exported = StateExporter(store).redacted(context(clock, "corr-export"))
    payload = exported.model_dump_json()

    assert connector_reference not in payload
    assert raw_health not in payload
    assert all(record.content_redacted for record in exported.records)
    assert store.audit_entries()[-1].action_class == "export.redacted"


def test_fake_provider_is_deterministic_and_matches_canonical_schema() -> None:
    request = ProviderRequest(
        schema_name="fixture.summary.v1",
        input_fingerprint="fingerprint-only",
    )
    response = ProviderResponse(
        schema_name="fixture.summary.v1",
        output_json='{"summary":"fixture output"}',
        provider_version="fake-v1",
    )
    provider = DeterministicFakeProvider((ProviderFixture(request=request, response=response),))

    assert provider.generate(request) == response
    assert provider.generate(request) == response

    schema_path = (
        Path(__file__).resolve().parents[1] / "contracts" / "v1" / "secretary-api.schema.json"
    )
    parsed = ContractSchema.model_validate_json(schema_path.read_bytes())
    assert set(parsed.definitions.provider_request.required) == set(ProviderRequest.model_fields)

    with pytest.raises(ProviderContractError):
        _ = provider.generate(
            ProviderRequest(schema_name="unknown.v1", input_fingerprint="unknown-fingerprint")
        )
