"""Small audited subset of the Hermes HTTP surface."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar, Final, Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, JsonValue, SecretStr

HERMES_REVISION: Final = "2237be355906fbe6065ce1815711eee52b2d646e"
HERMES_RELEASE: Final = "v2026.9.7"
MAX_RESPONSE_BYTES: Final = 2_000_000
MAX_REQUEST_BYTES: Final = 256_000
MAX_INPUT_CHARACTERS: Final = 16_000

type HermesEndpoint = Literal["/v1/capabilities", "/v1/toolsets", "/v1/chat/completions"]


class HermesRuntimeReview(BaseModel):
    """Trusted deployment review, never obtained from agent output or API headers.

    The evidence must cover no execution tools, restricted egress, temporary
    storage/memory disposal, and underlying model retention for this deployment.
    This model records the review; it cannot establish sandbox isolation itself.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    deployment_id: str
    revision: str
    evidence_reference: str
    verified_at: AwareDatetime
    valid_until: AwareDatetime


class ConnectorSecrets(Protocol):
    """Only the sidecar credential is available to this adapter."""

    def connector_secret(self, reference: str) -> str:
        """Resolve the deployment credential without exposing canonical-state keys."""
        ...


class HermesTransport(Protocol):
    """Bounded authenticated HTTP operations, without retries or redirect following."""

    def request(
        self,
        endpoint: HermesEndpoint,
        payload: dict[str, JsonValue] | None,
        credential: SecretStr,
        request_id: UUID,
    ) -> bytes:
        """GET discovery or POST one interpretation; never expose arbitrary paths."""
        ...


@dataclass(frozen=True)
class HermesSettings:
    """Operator-selected deployment and callbacks for current consent and review."""

    deployment_id: str
    model: str
    key_reference: str
    consent_granted: Callable[[], bool]
    runtime_review: Callable[[], HermesRuntimeReview | None]


class ApiAuth(BaseModel):
    """Required API authentication reported by capability discovery."""

    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True)
    type: Literal["bearer"]
    required: Literal[True]


class ApiFeatures(BaseModel):
    """The minimum runtime feature consumed by the first adapter."""

    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True)
    chat_completions: Literal[True]


class ApiCapabilities(BaseModel):
    """Hermes capabilities, not a claim of source revision or sandbox trust."""

    object: Literal["hermes.api_server.capabilities"]
    platform: Literal["hermes-agent"]
    auth: ApiAuth
    features: ApiFeatures


class Toolset(BaseModel):
    """Runtime toolset discovery; every enabled toolset rejects advisory operation."""

    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True)
    name: str
    enabled: bool
    tools: list[str]


class Toolsets(BaseModel):
    """Pinned endpoint envelope verified against upstream source."""

    object: Literal["list"]
    platform: Literal["api_server"]
    data: tuple[Toolset, ...]


class ChatMessage(BaseModel):
    """Only complete assistant text is consumed; tool calls never cross as authority."""

    role: Literal["assistant"]
    content: str
    tool_calls: tuple[JsonValue, ...] = ()
    function_call: JsonValue = None


class ChatChoice(BaseModel):
    """Require completed output, not a partial response or tool invocation."""

    message: ChatMessage
    finish_reason: Literal["stop"]


class ChatReply(BaseModel):
    """Selected chat response fields; provider metadata grants no LifeOS capabilities."""

    object: Literal["chat.completion"]
    choices: tuple[ChatChoice, ...]
