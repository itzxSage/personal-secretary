from pathlib import Path
from typing import ClassVar, Final

from pydantic import BaseModel, ConfigDict

from secretary_service.contract_version import CONTRACT_VERSION

ROOT: Final = Path(__file__).resolve().parents[1]
SCHEMA: Final = ROOT / "contracts" / "v1" / "secretary-api.schema.json"
SWIFT_VERSION: Final = (
    ROOT / "ios" / "SecretaryApp" / "SecretaryApp" / "Contract" / "ContractVersion.generated.swift"
)


class VersionDefinition(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    const: str


class ContractProperties(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    contract_version: VersionDefinition


class ContractSchema(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    properties: ContractProperties


def test_python_contract_version_matches_versioned_schema() -> None:
    # Given: the canonical versioned JSON Schema.
    schema = ContractSchema.model_validate_json(SCHEMA.read_bytes())

    # When: its machine-readable contract version is inspected.
    schema_version = schema.properties.contract_version.const

    # Then: generated Python code exposes the same version.
    assert schema_version == CONTRACT_VERSION


def test_swift_contract_version_matches_versioned_schema() -> None:
    # Given: the canonical versioned JSON Schema.
    schema = ContractSchema.model_validate_json(SCHEMA.read_bytes())
    schema_version = schema.properties.contract_version.const

    # When: the generated Swift source is inspected as a machine artifact.
    generated_source = SWIFT_VERSION.read_text(encoding="utf-8")

    # Then: Swift exposes the same structural contract version token.
    assert f'rawValue = "{schema_version}"' in generated_source
