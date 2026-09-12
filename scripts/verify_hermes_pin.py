"""Check the optional Hermes adapter manifest against its immutable source pin."""

from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from secretary_service.hermes.contracts import HERMES_RELEASE, HERMES_REVISION


class Manifest(BaseModel):
    """Closed default deployment policy; a fixture never enables the runtime."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    repository: Literal["https://github.com/NousResearch/hermes-agent"]
    release: str
    revision: str
    automatic_updates: Literal[False]
    role: Literal["planning-proposals-only"]
    allowed_endpoints: tuple[str, ...]
    canonical_state_access: Literal[False]
    external_actions: Literal[False]
    tool_execution: Literal[False]
    live_deployment_verified: Literal[False]


def verify() -> None:
    """Fail for manifest drift or unreviewed capability expansion."""
    root = Path(__file__).resolve().parents[1]
    manifest = Manifest.model_validate_json((root / "infra/hermes/runtime.json").read_bytes())
    endpoints = ("/v1/capabilities", "/v1/toolsets", "/v1/chat/completions")
    if (
        manifest.release != HERMES_RELEASE
        or manifest.revision != HERMES_REVISION
        or manifest.allowed_endpoints != endpoints
    ):
        message = "Hermes manifest does not match the reviewed adapter contract"
        raise ValueError(message)
    matrix = (root / "docs/capability-graft-matrix.md").read_text()
    if f"| Hermes Agent | {HERMES_REVISION} | MIT | integrate |" not in matrix:
        message = "Hermes pin is missing from the provenance matrix"
        raise ValueError(message)
    print("Hermes pin and advisory-only manifest verified; live deployment remains unverified")


if __name__ == "__main__":
    verify()
