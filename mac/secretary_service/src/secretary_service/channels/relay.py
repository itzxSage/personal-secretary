"""Ordered, idempotent text relay through the existing OpenClaw boundary."""

from typing import Final, final, override
from uuid import UUID

from secretary_service.channels.contracts import (
    ChannelAdapterDependencies,
    ChannelConfigurationError,
    ChannelCursorError,
    ChannelReplayError,
    ChannelSyncBatch,
    ChannelTextDelivery,
    DeliveryReceipt,
    DeliveryStatus,
)
from secretary_service.channels.events import ChannelEventFactory, TranscriptStage
from secretary_service.conversation_api import (
    ChannelKind,
    ConversationEvent,
    ConversationRejection,
)
from secretary_service.openclaw.contracts import (
    ChannelIdentityClaim,
    OpenClawReadRequest,
    OpenClawTextEnvelope,
)

MAX_SEQUENCE_REBASE_ATTEMPTS: Final = 3


@final
class _AppendRejectedError(Exception):
    """Internal signal carrying a typed canonical rejection."""

    rejection: ConversationRejection
    next_sequence: int

    def __init__(self, rejection: ConversationRejection, next_sequence: int) -> None:
        """Record a terminal canonical rejection and authoritative cursor."""
        super().__init__(rejection, next_sequence)
        self.rejection = rejection
        self.next_sequence = next_sequence

    @override
    def __str__(self) -> str:
        return f"canonical append rejected: {self.rejection.value}"


class TextChannelRelay:
    """Map channel text to canonical events without retaining canonical state."""

    _dependencies: ChannelAdapterDependencies
    _channel_kind: ChannelKind
    _events: ChannelEventFactory
    _pending: dict[str, ChannelTextDelivery]

    def __init__(
        self,
        dependencies: ChannelAdapterDependencies,
        channel_kind: ChannelKind,
    ) -> None:
        """Bind one enrolled channel kind to the isolated OpenClaw adapter."""
        binding = dependencies.identity.binding
        if binding.channel_kind is not channel_kind:
            raise ChannelConfigurationError(channel_kind)
        self._dependencies = dependencies
        self._channel_kind = channel_kind
        self._events = ChannelEventFactory(dependencies.identity)
        self._pending = {}

    @property
    def pending_count(self) -> int:
        """Return raw external deliveries waiting for Life Engine connectivity."""
        return len(self._pending)

    def receive_delivery(self, delivery: ChannelTextDelivery) -> DeliveryReceipt:
        """Forward a delivery now, or retain only its raw transport form offline."""
        if delivery.channel_kind is not self._channel_kind:
            raise ChannelConfigurationError(delivery.channel_kind)
        queued = self._pending.get(delivery.delivery_id)
        if queued is not None:
            if queued != delivery:
                raise ChannelReplayError(delivery.delivery_id)
            return self._receipt(delivery, DeliveryStatus.QUEUED, None)
        try:
            receipt = self._deliver(delivery)
        except ConnectionError:
            self._pending[delivery.delivery_id] = delivery
            return self._receipt(delivery, DeliveryStatus.QUEUED, None)
        _ = self._pending.pop(delivery.delivery_id, None)
        return receipt

    def reconnect(self) -> tuple[DeliveryReceipt, ...]:
        """Flush pending deliveries in arrival order after transport recovery."""
        receipts: list[DeliveryReceipt] = []
        for delivery in tuple(self._pending.values()):
            _ = self._pending.pop(delivery.delivery_id)
            receipt = self.receive_delivery(delivery)
            receipts.append(receipt)
            match receipt.status:  # noqa: MATCH_OK - basedpyright proves enum exhaustiveness.
                case DeliveryStatus.QUEUED:
                    return tuple(receipts)
                case DeliveryStatus.ACCEPTED | DeliveryStatus.DUPLICATE | DeliveryStatus.REJECTED:
                    continue
        return tuple(receipts)

    def synchronize(self, after_sequence: int) -> ChannelSyncBatch:
        """Replay canonical cross-device events after the caller's durable cursor."""
        if after_sequence < 0:
            raise ChannelCursorError(after_sequence)
        events = self._dependencies.openclaw.events(
            OpenClawReadRequest(claim=self._configured_claim()),
            self._dependencies.client,
        )
        next_sequence = events[-1].sequence + 1 if events else 1
        return ChannelSyncBatch(
            after_sequence=after_sequence,
            next_sequence=next_sequence,
            events=tuple(event for event in events if event.sequence > after_sequence),
        )

    def _deliver(self, delivery: ChannelTextDelivery) -> DeliveryReceipt:
        claim = self._claim_for(delivery)
        remote = self._dependencies.openclaw.events(
            OpenClawReadRequest(claim=claim),
            self._dependencies.client,
        )
        remote_by_id = {event.event_id: event for event in remote}
        partial_id = self._events.event_id(delivery, TranscriptStage.PARTIAL)
        final_id = self._events.event_id(delivery, TranscriptStage.FINAL)
        partial = remote_by_id.get(partial_id)
        final = remote_by_id.get(final_id)
        if final is not None and partial is None:
            raise ChannelReplayError(delivery.delivery_id)
        if partial is not None:
            self._verify_replay(delivery, TranscriptStage.PARTIAL, partial)
        if final is not None:
            self._verify_replay(delivery, TranscriptStage.FINAL, final)
        if partial is not None and final is not None:
            return self._receipt(delivery, DeliveryStatus.DUPLICATE, final.sequence + 1)

        next_sequence = remote[-1].sequence + 1 if remote else 1
        try:
            if partial is None:
                partial, next_sequence = self._append_stage(
                    delivery,
                    TranscriptStage.PARTIAL,
                    next_sequence,
                    claim,
                )
            if final is None:
                final, next_sequence = self._append_stage(
                    delivery,
                    TranscriptStage.FINAL,
                    next_sequence,
                    claim,
                )
        except _AppendRejectedError as error:
            return DeliveryReceipt(
                delivery_id=delivery.delivery_id,
                status=DeliveryStatus.REJECTED,
                canonical_event_ids=(partial_id, final_id),
                next_sequence=error.next_sequence,
                rejection=error.rejection,
            )
        return self._receipt(delivery, DeliveryStatus.ACCEPTED, next_sequence)

    def _append_stage(
        self,
        delivery: ChannelTextDelivery,
        stage: TranscriptStage,
        sequence: int,
        claim: ChannelIdentityClaim,
    ) -> tuple[ConversationEvent, int]:
        event = self._events.event(delivery, stage, sequence)
        for _attempt in range(MAX_SEQUENCE_REBASE_ATTEMPTS):
            result = self._dependencies.openclaw.append_text(
                OpenClawTextEnvelope(claim=claim, event=event),
                self._dependencies.client,
            )
            if result.accepted:
                return event, result.next_sequence
            match result.rejection:  # noqa: MATCH_OK - all typed outcomes are explicit.
                case ConversationRejection.OUT_OF_ORDER:
                    event = event.model_copy(update={"sequence": result.next_sequence})
                case ConversationRejection.DUPLICATE_EVENT:
                    replayed = self._canonical_event(claim, event.event_id)
                    self._verify_replay(delivery, stage, replayed)
                    return replayed, result.next_sequence
                case (
                    ConversationRejection.UNKNOWN_CONVERSATION
                    | ConversationRejection.CONVERSATION_CLOSED
                    | ConversationRejection.FOREIGN_PARTICIPANT
                    | ConversationRejection.FOREIGN_DEVICE
                    | ConversationRejection.DEVICE_PARTICIPANT_MISMATCH
                    | ConversationRejection.KIND_MISMATCH
                    | ConversationRejection.FINAL_WITHOUT_PARTIAL
                    | ConversationRejection.TURN_CLOSED
                    | ConversationRejection.EVENT_AFTER_CANCELLATION
                    | ConversationRejection.RESUME_WITHOUT_CANCELLATION
                ) as rejection:
                    raise _AppendRejectedError(rejection, result.next_sequence)
                case None:
                    raise ChannelReplayError(delivery.delivery_id)
        raise _AppendRejectedError(ConversationRejection.OUT_OF_ORDER, event.sequence)

    def _canonical_event(
        self,
        claim: ChannelIdentityClaim,
        event_id: UUID,
    ) -> ConversationEvent:
        events = self._dependencies.openclaw.events(
            OpenClawReadRequest(claim=claim),
            self._dependencies.client,
        )
        event = next((item for item in events if item.event_id == event_id), None)
        if event is None:
            raise ChannelReplayError(str(event_id))
        return event

    def _verify_replay(
        self,
        delivery: ChannelTextDelivery,
        stage: TranscriptStage,
        existing: ConversationEvent,
    ) -> None:
        expected = self._events.event(delivery, stage, existing.sequence)
        if existing != expected:
            raise ChannelReplayError(delivery.delivery_id)

    def _claim_for(self, delivery: ChannelTextDelivery) -> ChannelIdentityClaim:
        return ChannelIdentityClaim(
            binding_id=self._dependencies.identity.binding.binding_id,
            channel_kind=delivery.channel_kind,
            external_channel_id=delivery.external_channel_id,
            external_user_id=delivery.external_user_id,
        )

    def _configured_claim(self) -> ChannelIdentityClaim:
        binding = self._dependencies.identity.binding
        return ChannelIdentityClaim(
            binding_id=binding.binding_id,
            channel_kind=binding.channel_kind,
            external_channel_id=binding.external_channel_id,
            external_user_id=binding.external_user_id,
        )

    def _receipt(
        self,
        delivery: ChannelTextDelivery,
        status: DeliveryStatus,
        next_sequence: int | None,
    ) -> DeliveryReceipt:
        return DeliveryReceipt(
            delivery_id=delivery.delivery_id,
            status=status,
            canonical_event_ids=(
                self._events.event_id(delivery, TranscriptStage.PARTIAL),
                self._events.event_id(delivery, TranscriptStage.FINAL),
            ),
            next_sequence=next_sequence,
        )
