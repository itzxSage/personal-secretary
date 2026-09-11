"""Private mTLS conversation ingress. No provider calls, tools, approvals, or mutations."""

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractContextManager, asynccontextmanager
from typing import Annotated, ClassVar, cast, final
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ConfigDict, Field, ValidationError
from sqlcipher3 import dbapi2 as sqlcipher

from secretary_service.conversation_api import (
    CONVERSATION_CONTRACT_VERSION,
    Conversation,
    ConversationEvent,
)
from secretary_service.enrollment import DeviceRegistry
from secretary_service.models import ActorId, CorrelationId, FrozenModel, TransitionContext
from secretary_service.relay_auth import (
    RequestAuthenticationError,
    RequestAuthenticator,
    RequestProof,
)
from secretary_service.relay_store import RelayAccessError, RelayConflictError
from secretary_service.storage import Clock, EncryptedStateStore

MAX_BODY_BYTES = 256 * 1024


class CreateConversation(FrozenModel):
    """Caller may choose a retry-stable ID and title, but never its own membership."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    conversation_id: UUID
    title: str = Field(min_length=1, max_length=200)


class EventBatch(FrozenModel):
    """Bounded delivery unit; all new events commit or none do."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    events: list[ConversationEvent] = Field(min_length=1, max_length=100)


class EventAcknowledgment(FrozenModel):
    """Only IDs in this successful acknowledgment may leave the client outbox."""

    acknowledged_event_ids: list[UUID]
    next_sequence: int


class EventPage(FrozenModel):
    """Resume from the last returned sequence, not from a speculative client count."""

    events: list[ConversationEvent]
    cursor: int


async def _body(request: Request) -> bytes:
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise HTTPException(413, "request too large")
        chunks.append(chunk)
    return b"".join(chunks)


async def _invalid_request(_request: Request, _error: Exception) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": "invalid request parameters"})


def _request_identity(request: Request) -> tuple[RequestProof, str, str]:
    # Only the TLS protocol adapter sets this attribute. Proxy headers never do.
    peer: object = getattr(request.state, "lifeos_peer_fingerprint", None)
    if request.scope["scheme"] != "https" or not isinstance(peer, str):
        raise RequestAuthenticationError
    names = ("x-lifeos-device", "x-lifeos-request-id", "x-lifeos-issued-at", "x-lifeos-signature")
    if any(len(request.headers.getlist(name)) != 1 for name in names):
        raise RequestAuthenticationError
    proof = RequestProof(
        device_id=UUID(request.headers[names[0]]),
        request_id=UUID(request.headers[names[1]]),
        issued_at=int(request.headers[names[2]]),
        signature=request.headers[names[3]],
    )
    raw_path = cast("object", request.scope["raw_path"])
    query = cast("object", request.scope["query_string"])
    if not isinstance(raw_path, bytes) or not isinstance(query, bytes):
        raise RequestAuthenticationError
    target = raw_path.decode("ascii") + ("?" + query.decode("ascii") if query else "")
    return proof, peer, target


@final
class RelayRoutes:
    """HTTP handlers sharing one lifespan-owned encrypted connection."""

    def __init__(self, stores: list[EncryptedStateStore], clock: Clock) -> None:
        """Bind the lifespan store slot and clock without opening SQLite on another thread."""
        self.stores = stores
        self.clock = clock

    async def authenticate(self, request: Request) -> tuple[bytes, UUID, TransitionContext]:
        """Authenticate the exact bounded body before parsing any caller content."""
        try:
            raw = await asyncio.wait_for(_body(request), timeout=10)
        except TimeoutError as error:
            raise HTTPException(408, "request body timed out") from error
        if request.headers.get("x-lifeos-contract-version") != CONVERSATION_CONTRACT_VERSION:
            raise HTTPException(426, "unsupported conversation contract")
        try:
            proof, peer, target = _request_identity(request)
            auth = RequestAuthenticator(
                DeviceRegistry(self.clock, self.stores[0].devices),
                self.stores[0].authority_consumption,
                self.clock,
            )
            device_id = auth.verify(proof, request.method, target, raw, peer)
        except (ValueError, UnicodeError, RequestAuthenticationError) as error:
            raise HTTPException(401, "device authentication failed") from error
        context = TransitionContext(
            actor=ActorId(str(device_id)),
            correlation_id=CorrelationId(str(proof.request_id)),
            occurred_at=self.clock.now(),
        )
        return raw, device_id, context

    async def create(self, request: Request) -> Conversation:
        """Create or acknowledge a private conversation."""
        raw, device_id, context = await self.authenticate(request)
        try:
            value = CreateConversation.model_validate_json(raw)
            return self.stores[0].conversations.create(
                value.conversation_id, value.title, device_id, context
            )
        except ValidationError as error:
            raise HTTPException(422, "invalid conversation") from error
        except RelayAccessError as error:
            raise HTTPException(403, "conversation unavailable") from error
        except (RelayConflictError, sqlcipher.IntegrityError) as error:
            raise HTTPException(409, "conversation conflict") from error

    async def append(self, conversation_id: UUID, request: Request) -> EventAcknowledgment:
        """Acknowledge only an atomically committed batch."""
        raw, device_id, context = await self.authenticate(request)
        try:
            batch = EventBatch.model_validate_json(raw)
            next_sequence = self.stores[0].conversations.append(
                conversation_id, device_id, batch.events, context
            )
            return EventAcknowledgment(
                acknowledged_event_ids=[event.event_id for event in batch.events],
                next_sequence=next_sequence,
            )
        except ValidationError as error:
            raise HTTPException(422, "invalid event batch") from error
        except RelayAccessError as error:
            raise HTTPException(403, "conversation unavailable") from error
        except (RelayConflictError, sqlcipher.IntegrityError) as error:
            raise HTTPException(
                409, "event sequence or content conflict; resume before retrying"
            ) from error

    async def read(
        self,
        conversation_id: UUID,
        request: Request,
        after: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
    ) -> EventPage:
        """Return a bounded, authorized resume page."""
        _, device_id, _ = await self.authenticate(request)
        try:
            events = self.stores[0].conversations.events(
                conversation_id, device_id, after=after, limit=limit
            )
            return EventPage(events=events, cursor=events[-1].sequence if events else after)
        except RelayAccessError as error:
            raise HTTPException(403, "conversation unavailable") from error

    async def delete(self, conversation_id: UUID, request: Request) -> dict[str, bool]:
        """Purge authorized content; signed empty-body requests are replay protected."""
        raw, device_id, context = await self.authenticate(request)
        if raw:
            raise HTTPException(422, "deletion request body must be empty")
        try:
            _ = self.stores[0].conversations.delete(conversation_id, device_id, context)
        except RelayAccessError as error:
            raise HTTPException(403, "conversation unavailable") from error
        else:
            return {"deleted": True}


def create_relay_app(
    open_store: Callable[[], AbstractContextManager[EncryptedStateStore]], clock: Clock
) -> FastAPI:
    """Open encrypted state on the event-loop thread; fail closed without trusted TLS state."""
    stores: list[EncryptedStateStore] = []

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        with open_store() as store:
            stores.append(store)
            try:
                yield
            finally:
                stores.clear()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_exception_handler(RequestValidationError, _invalid_request)
    routes = RelayRoutes(stores, clock)
    app.add_api_route("/v1/conversations", routes.create, methods=["POST"])
    app.add_api_route("/v1/conversations/{conversation_id}/events", routes.append, methods=["POST"])
    app.add_api_route("/v1/conversations/{conversation_id}/events", routes.read, methods=["GET"])
    app.add_api_route("/v1/conversations/{conversation_id}", routes.delete, methods=["DELETE"])
    return app
