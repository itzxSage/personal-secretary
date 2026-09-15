"""PostgreSQL conversation journal preserving the existing phone wire contracts."""

import hmac
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, final
from uuid import UUID

import psycopg

from secretary_service.audit import AuditMutation
from secretary_service.canonical import CanonicalIdentity, ConversationRecord
from secretary_service.canonical import ConversationEvent as CanonicalEvent
from secretary_service.cloud_crypto import CloudCipher
from secretary_service.conversation_api import (
    Conversation,
    ConversationDevice,
    ConversationEvent,
    ConversationJournal,
    ConversationState,
)
from secretary_service.enrollment import Device, DeviceState
from secretary_service.models import RecordId, RecordKind, TransitionContext
from secretary_service.relay_store import (
    CLIENT_KINDS,
    CONVERSATION_RETENTION,
    MAX_BATCH_EVENTS,
    MAX_CONVERSATION_EVENTS,
    MAX_EVENT_BYTES,
    RelayAccessError,
    RelayConflictError,
)

if TYPE_CHECKING:
    from secretary_service.device_persistence import DevicePersistence
    from secretary_service.postgres_types import PgConnection


@final
class PostgresConversations:
    """Encrypt canonical conversation records and enforce access within the cell lock."""

    def __init__(
        self,
        connection: "PgConnection",
        cipher: CloudCipher,
        devices: "DevicePersistence",
        audit: Callable[[AuditMutation], None],
        require_active: Callable[[], None],
    ) -> None:
        """Bind only to repositories owned by one active execution transaction."""
        self._connection = connection
        self._cipher = cipher
        self._devices = devices
        self._append_audit = audit
        self._require_active = require_active

    def _require_enrolled(self, device_id: UUID) -> None:
        record = self._devices.load(str(device_id))
        if record is None or Device.model_validate_json(record).state != DeviceState.ENROLLED:
            raise RelayAccessError

    def provision(
        self, identity: CanonicalIdentity, device: ConversationDevice, context: TransitionContext
    ) -> None:
        """Bind an already-enrolled device to an operator-selected canonical participant."""
        self._require_active()
        self._require_enrolled(device.device_id)
        if identity.record_id != device.participant_id:
            raise RelayAccessError
        with self._connection.transaction():
            row = self._connection.execute(
                "SELECT envelope FROM lifeos_relay_identities WHERE participant_id=%s",
                (identity.record_id,),
            ).fetchone()
            location = ("participant", str(identity.record_id))
            if row is None:
                _ = self._connection.execute(
                    "INSERT INTO lifeos_relay_identities VALUES (%s, %s)",
                    (identity.record_id, self._cipher.seal(identity.model_dump_json(), location)),
                )
            elif (
                CanonicalIdentity.model_validate_json(self._cipher.open(row[0], location))
                != identity
            ):
                raise RelayConflictError
            existing = self.device(device.device_id)
            if existing is not None:
                if existing != device:
                    raise RelayConflictError
                return
            _ = self._connection.execute(
                "INSERT INTO lifeos_relay_devices VALUES (%s, %s, %s)",
                (
                    device.device_id,
                    device.participant_id,
                    self._cipher.seal(
                        device.model_dump_json(), ("relay-device", str(device.device_id))
                    ),
                ),
            )
            self._audit(device.device_id, "device.bound", device.model_dump_json(), context)

    def device(self, device_id: UUID) -> ConversationDevice | None:
        """Read the canonical participant binding for an active enrolled device."""
        self._require_active()
        self._require_enrolled(device_id)
        row = self._connection.execute(
            "SELECT envelope FROM lifeos_relay_devices WHERE device_id=%s", (device_id,)
        ).fetchone()
        return (
            None
            if row is None
            else ConversationDevice.model_validate_json(
                self._cipher.open(row[0], ("relay-device", str(device_id)))
            )
        )

    def create(
        self, conversation_id: UUID, title: str, device_id: UUID, context: TransitionContext
    ) -> Conversation:
        """Create or acknowledge an exact retry; deleted conversation IDs stay unavailable."""
        device = self.device(device_id)
        if device is None:
            raise RelayAccessError
        deletion_query = "SELECT conversation_id::text FROM lifeos_conversation_deletions WHERE conversation_id=%s"  # noqa: E501 - static SQL
        with self._connection.transaction():
            deleted = self._connection.execute(
                deletion_query,
                (conversation_id,),
            ).fetchone()
            if deleted is not None:
                raise RelayConflictError
            exists = self._connection.execute(
                "SELECT conversation_id::text FROM lifeos_conversations WHERE conversation_id=%s",
                (conversation_id,),
            ).fetchone()
            if exists is not None:
                current = self.conversation(conversation_id, device_id)
                if current.title != title:
                    raise RelayConflictError
                return current
            record = ConversationRecord(
                record_id=RecordId(conversation_id),
                created_at=context.occurred_at,
                state="active",
                title=title,
                participant_ids=(RecordId(device.participant_id),),
            )
            _ = self._connection.execute(
                "INSERT INTO lifeos_conversations VALUES (%s, %s, %s)",
                (
                    conversation_id,
                    context.occurred_at + CONVERSATION_RETENTION,
                    self._cipher.seal(
                        record.model_dump_json(), ("conversation", str(conversation_id))
                    ),
                ),
            )
            self._audit(conversation_id, "conversation.created", record.model_dump_json(), context)
            return self.conversation(conversation_id, device_id)

    def _devices_for(self, participant_ids: tuple[RecordId, ...]) -> list[ConversationDevice]:
        rows = self._connection.execute(
            "SELECT device_id::text, envelope FROM lifeos_relay_devices"
        ).fetchall()
        devices = [
            ConversationDevice.model_validate_json(
                self._cipher.open(row[1], ("relay-device", row[0]))
            )
            for row in rows
        ]
        return [device for device in devices if device.participant_id in participant_ids]

    def conversation(self, conversation_id: UUID, device_id: UUID) -> Conversation:
        """Check current device membership before releasing conversation metadata."""
        device = self.device(device_id)
        row = self._connection.execute(
            "SELECT envelope FROM lifeos_conversations WHERE conversation_id=%s", (conversation_id,)
        ).fetchone()
        if device is None or row is None:
            raise RelayAccessError
        record = ConversationRecord.model_validate_json(
            self._cipher.open(row[0], ("conversation", str(conversation_id)))
        )
        if device.participant_id not in record.participant_ids:
            raise RelayAccessError
        devices = self._devices_for(record.participant_ids)
        return Conversation(
            conversation_id=conversation_id,
            title=record.title or "",
            participant_ids=list(record.participant_ids),
            device_ids=[bound.device_id for bound in devices],
            created_at=record.created_at,
            state=ConversationState(record.state),
        )

    def events(
        self, conversation_id: UUID, device_id: UUID, *, after: int = 0, limit: int = 100
    ) -> list[ConversationEvent]:
        """Return an authorized bounded page, preserving durable sequence cursors."""
        _ = self.conversation(conversation_id, device_id)
        if after < 0 or not 1 <= limit <= MAX_CONVERSATION_EVENTS:
            raise RelayConflictError
        rows = self._connection.execute(
            """SELECT event_id::text, sequence::text, envelope FROM lifeos_conversation_events
            WHERE conversation_id=%s AND sequence>%s ORDER BY sequence LIMIT %s""",
            (conversation_id, after, limit),
        ).fetchall()
        result: list[ConversationEvent] = []
        for row in rows:
            record = CanonicalEvent.model_validate_json(
                self._cipher.open(
                    row[2], ("conversation-event", str(conversation_id), row[0], row[1])
                )
            )
            result.append(ConversationEvent.model_validate_json(record.payload))
        return result

    def append(
        self,
        conversation_id: UUID,
        device_id: UUID,
        events: list[ConversationEvent],
        context: TransitionContext,
    ) -> int:
        """Commit a complete ordered batch or none; exact retries add no content or audit."""
        self._require_active()
        try:
            with self._connection.transaction():
                return self._append(conversation_id, device_id, events, context)
        except psycopg.errors.UniqueViolation:
            raise RelayConflictError from None

    def _append(
        self,
        conversation_id: UUID,
        device_id: UUID,
        events: list[ConversationEvent],
        context: TransitionContext,
    ) -> int:
        conversation = self.conversation(conversation_id, device_id)
        devices = self._devices_for(
            tuple(RecordId(value) for value in conversation.participant_ids)
        )
        journal = ConversationJournal(conversation, devices)
        history = self.events(conversation_id, device_id, limit=MAX_CONVERSATION_EVENTS)
        known = {event.event_id: event for event in history}
        for event in history:
            if not journal.accept(event).accepted:
                raise RelayConflictError
        if not 1 <= len(events) <= MAX_BATCH_EVENTS or len({e.event_id for e in events}) != len(
            events
        ):
            raise RelayConflictError
        for event in events:
            if event.device_id != device_id or event.kind not in CLIENT_KINDS:
                raise RelayAccessError
            if (
                event.occurred_at.tzinfo is None
                or len(event.model_dump_json().encode()) > MAX_EVENT_BYTES
            ):
                raise RelayConflictError
            if event.event_id in known:
                if event != known[event.event_id]:
                    raise RelayConflictError
                continue
            if len(known) >= MAX_CONVERSATION_EVENTS or not journal.accept(event).accepted:
                raise RelayConflictError
            self._insert_event(event, context)
            known[event.event_id] = event
        return len(known) + 1

    def _insert_event(self, event: ConversationEvent, context: TransitionContext) -> None:
        record = CanonicalEvent(
            record_id=RecordId(event.event_id),
            created_at=event.occurred_at,
            state="accepted",
            conversation_id=RecordId(event.conversation_id),
            sequence=event.sequence,
            event_type="relay.v1",
            payload=event.model_dump_json(),
        )
        _ = self._connection.execute(
            "INSERT INTO lifeos_conversation_events VALUES (%s, %s, %s, %s)",
            (
                event.event_id,
                event.conversation_id,
                event.sequence,
                self._cipher.seal(
                    record.model_dump_json(),
                    (
                        "conversation-event",
                        str(event.conversation_id),
                        str(event.event_id),
                        str(event.sequence),
                    ),
                ),
            ),
        )
        self._audit(event.event_id, "conversation.event.accepted", record.payload, context)

    def delete(self, conversation_id: UUID, device_id: UUID, context: TransitionContext) -> int:
        """Purge authorized content and preserve a deletion marker that prevents recreation."""
        _ = self.conversation(conversation_id, device_id)
        return self._delete(conversation_id, context)

    def expire(self, now: datetime, context: TransitionContext) -> int:
        """Expire conversations under the same transaction as deletion and audit."""
        self._require_active()
        rows = self._connection.execute(
            "SELECT conversation_id::text FROM lifeos_conversations WHERE expires_at<=%s",
            (now,),
        ).fetchall()
        for row in rows:
            _ = self._delete(UUID(row[0]), context)
        return len(rows)

    def _delete(self, conversation_id: UUID, context: TransitionContext) -> int:
        with self._connection.transaction():
            row = self._connection.execute(
                "SELECT count(*)::text FROM lifeos_conversation_events WHERE conversation_id=%s",
                (conversation_id,),
            ).fetchone()
            count = 0 if row is None else int(row[0])
            _ = self._connection.execute(
                "INSERT INTO lifeos_conversation_deletions VALUES (%s)", (conversation_id,)
            )
            _ = self._connection.execute(
                "DELETE FROM lifeos_conversations WHERE conversation_id=%s", (conversation_id,)
            )
            self._audit(
                conversation_id, "conversation.deleted", f"{conversation_id}:{count}", context
            )
            return count

    def _audit(
        self, record_id: UUID, action: str, content: str, context: TransitionContext
    ) -> None:
        self._append_audit(
            AuditMutation(
                record_id=RecordId(record_id),
                record_kind=RecordKind.EVENT,
                state="accepted",
                action_class=action,
                source_fingerprint=hmac.digest(
                    self._cipher.keys.audit.get_secret_value(), content.encode(), "sha256"
                ).hex(),
                context=context,
            )
        )
