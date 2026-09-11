"""Out-of-process OpenClaw adapter restricted to typed Conversation API calls."""

from dataclasses import dataclass
from typing import final, override

from secretary_service.conversation_api import (
    Conversation,
    ConversationAcceptance,
    ConversationEvent,
    TranscriptFinal,
    TranscriptPartial,
)
from secretary_service.enrollment import DeviceRegistry
from secretary_service.openclaw.bindings import (
    BindingRejection,
    BindingViolationError,
    IdentityBindingRegistry,
)
from secretary_service.openclaw.contracts import (
    ALLOWED_CAPABILITIES,
    ALLOWED_ENDPOINTS,
    AccessDecision,
    AdapterEndpoint,
    AdapterHealth,
    ConversationApi,
    MtlsClientIdentity,
    OpenClawAdapterConfig,
    OpenClawReadRequest,
    OpenClawTextEnvelope,
    adapter_health,
)


@final
class OpenClawAuthenticationError(Exception):
    """The presented mTLS identity is not the pinned service account."""

    def __init__(self, device_id: str) -> None:
        """Record the rejected device without exposing its fingerprint."""
        super().__init__(device_id)
        self.device_id = device_id

    @override
    def __str__(self) -> str:
        return f"device {self.device_id} is not the OpenClaw service account"


@dataclass(frozen=True, slots=True)
class OpenClawAdapterDependencies:
    """Injected typed API and identity registries; no state or key provider exists."""

    api: ConversationApi
    devices: DeviceRegistry
    bindings: IdentityBindingRegistry


@final
class OpenClawAdapter:
    """Authenticate, bind, and forward OpenClaw text without canonical state access."""

    def __init__(
        self,
        config: OpenClawAdapterConfig,
        dependencies: OpenClawAdapterDependencies,
    ) -> None:
        """Bind the pinned service account to the injected typed API and registries."""
        self._config = config
        self._api = dependencies.api
        self._devices = dependencies.devices
        self._bindings = dependencies.bindings

    @property
    def allowed_endpoints(self) -> tuple[AdapterEndpoint, ...]:
        """Return the closed typed Conversation API endpoint allowlist."""
        return ALLOWED_ENDPOINTS

    def endpoint_access(self, endpoint: str) -> AccessDecision:
        """Reject any operation outside the typed Conversation API."""
        allowed = endpoint in {item.value for item in ALLOWED_ENDPOINTS}
        reason = "typed Conversation API endpoint" if allowed else "endpoint is not allowlisted"
        return AccessDecision(allowed=allowed, endpoint=endpoint, reason=reason)

    def capability_access(self, capability: str) -> AccessDecision:
        """Reject database, filesystem, key, worker, and other capability escalation."""
        allowed = capability in {item.value for item in ALLOWED_CAPABILITIES}
        reason = "conversation capability" if allowed else "capability is not granted"
        return AccessDecision(allowed=allowed, endpoint=capability, reason=reason)

    def health(self, client: MtlsClientIdentity) -> AdapterHealth:
        """Return compatibility health only to the enrolled service account."""
        self._authenticate(client)
        return adapter_health()

    def conversation(
        self,
        request: OpenClawReadRequest,
        client: MtlsClientIdentity,
    ) -> Conversation:
        """Read only the conversation named by an exact active binding."""
        self._authenticate(client)
        identity = self._bindings.resolve(request.claim, client.device_id)
        return self._api.get_conversation(identity.binding.conversation_id)

    def events(
        self,
        request: OpenClawReadRequest,
        client: MtlsClientIdentity,
    ) -> tuple[ConversationEvent, ...]:
        """Read events only for the conversation named by an exact active binding."""
        self._authenticate(client)
        identity = self._bindings.resolve(request.claim, client.device_id)
        return self._api.list_events(identity.binding.conversation_id)

    def append_text(
        self,
        envelope: OpenClawTextEnvelope,
        client: MtlsClientIdentity,
    ) -> ConversationAcceptance:
        """Forward one bound WebChat or Telegram text event through the typed API."""
        self._authenticate(client)
        identity = self._bindings.resolve(envelope.claim, client.device_id)
        event = envelope.event
        event_identity_matches = (
            event.conversation_id == identity.binding.conversation_id
            and event.participant_id == identity.participant_id
            and event.device_id == identity.device_id
        )
        if not event_identity_matches:
            raise BindingViolationError(
                envelope.claim.binding_id,
                BindingRejection.IDENTITY_MISMATCH,
            )
        match event.payload:
            case TranscriptPartial() | TranscriptFinal():
                return self._api.append_event(event)
            case _:
                raise BindingViolationError(
                    envelope.claim.binding_id,
                    BindingRejection.UNSUPPORTED_CHANNEL,
                )

    def _authenticate(self, client: MtlsClientIdentity) -> None:
        _ = self._devices.verify_mtls_identity(
            client.device_id,
            client.public_key_fingerprint,
        )
        if client.device_id != self._config.service_account_device_id:
            raise OpenClawAuthenticationError(str(client.device_id))
