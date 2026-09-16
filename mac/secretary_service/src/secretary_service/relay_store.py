"""Atomic encrypted conversation ingestion with canonical content and durable retries."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, final
from uuid import UUID, uuid4

from secretary_service.audit import AuditMutation
from secretary_service.canonical import CanonicalIdentity, ConversationRecord
from secretary_service.canonical import ConversationEvent as CanonicalEvent
from secretary_service.conversation_api import (
    Conversation,
    ConversationDevice,
    ConversationEvent,
    ConversationEventKind,
    ConversationJournal,
    ConversationState,
)
from secretary_service.models import RecordId, RecordKind, TransitionContext

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlcipher3 import dbapi2 as sqlcipher

    from secretary_service.keys import KeyProvider
    from secretary_service.ledger import AuditLedger

MAX_CONVERSATION_EVENTS = 10_000
MAX_BATCH_EVENTS = 100
MAX_EVENT_BYTES = 16 * 1024
CONVERSATION_RETENTION = timedelta(days=180)
CLIENT_KINDS = frozenset(
    {
        ConversationEventKind.TRANSCRIPT_PARTIAL,
        ConversationEventKind.TRANSCRIPT_FINAL,
        ConversationEventKind.CANCELLATION,
        ConversationEventKind.RESUME,
    }
)


class RelayConflictError(Exception):
    """An event batch conflicts with the durable stream; no new event was committed."""


@dataclass(frozen=True)
class AgentTurnPersistence:
    """One compare-and-swap persistence request for an assistant turn."""

    conversation_id: UUID
    device_id: UUID
    expected_revision: int
    text: str
    reply: str
    context: TransitionContext


class RelayAccessError(Exception):
    """The authenticated device has no access to the requested conversation."""


@final
class ConversationRelayStore:
    """Keep canonical content, ordering indexes, and minimized audit facts atomic."""

    def __init__(
        self,
        connection: sqlcipher.Connection,
        keys: KeyProvider,
        ledger: AuditLedger,
        authorize_delete: Callable[[], None],
        revoke_delete: Callable[[], None],
    ) -> None:
        """Use the owning encrypted store's connection and ledger."""
        self._connection = connection
        self._keys = keys
        self._ledger = ledger
        self._authorize_delete = authorize_delete
        self._revoke_delete = revoke_delete

    def provision(
        self,
        identity: CanonicalIdentity,
        device: ConversationDevice,
        context: TransitionContext,
    ) -> None:
        """Locally bind an already-enrolled device; never exposed through HTTP."""
        if identity.record_id != device.participant_id:
            raise RelayAccessError
        with self._connection:
            _ = self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                """SELECT content_json FROM canonical_identities WHERE record_id=?
                ORDER BY version DESC LIMIT 1""",
                (str(identity.record_id),),
            ).fetchone()
            if row is None:
                _ = self._connection.execute(
                    "INSERT INTO canonical_identities VALUES (?, 1, ?, ?, ?)",
                    (
                        str(identity.record_id),
                        identity.state,
                        identity.model_dump_json(),
                        identity.created_at.isoformat(),
                    ),
                )
            elif CanonicalIdentity.model_validate_json(str(row[0])) != identity:
                raise RelayConflictError
            existing = self.device(device.device_id)
            if existing is not None:
                if existing != device:
                    raise RelayConflictError
                return
            _ = self._connection.execute(
                "INSERT INTO relay_devices VALUES (?, ?)",
                (str(device.device_id), device.model_dump_json()),
            )
            self._audit(device.device_id, "device.bound", device.model_dump_json(), context)

    def device(self, device_id: UUID) -> ConversationDevice | None:
        """Resolve a trusted local participant binding, never a caller-supplied one."""
        row = self._connection.execute(
            "SELECT record_json FROM relay_devices WHERE device_id=?", (str(device_id),)
        ).fetchone()
        return None if row is None else ConversationDevice.model_validate_json(str(row[0]))

    def create(
        self, conversation_id: UUID, title: str, device_id: UUID, context: TransitionContext
    ) -> Conversation:
        """Create a private conversation, or acknowledge an identical creation retry."""
        device = self.device(device_id)
        if device is None:
            raise RelayAccessError
        with self._connection:
            _ = self._connection.execute("BEGIN IMMEDIATE")
            exists = self._connection.execute(
                "SELECT 1 FROM canonical_conversations WHERE record_id=?",
                (str(conversation_id),),
            ).fetchone()
            if exists:
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
                "INSERT INTO canonical_conversations VALUES (?, 1, ?, ?, ?)",
                (
                    str(conversation_id),
                    record.state,
                    record.model_dump_json(),
                    record.created_at.isoformat(),
                ),
            )
            _ = self._connection.execute(
                "INSERT INTO relay_conversations VALUES (?)", (str(conversation_id),)
            )
            _ = self._connection.execute(
                "INSERT INTO relay_retention VALUES (?, ?)",
                (
                    str(conversation_id),
                    (context.occurred_at + CONVERSATION_RETENTION).isoformat(),
                ),
            )
            self._audit(conversation_id, "conversation.created", record.model_dump_json(), context)
        return self.conversation(conversation_id, device_id)

    def delete(self, conversation_id: UUID, device_id: UUID, context: TransitionContext) -> int:
        """Purge authorized content and retain only content-minimized audit proof."""
        _ = self.conversation(conversation_id, device_id)
        return self._delete(conversation_id, context)

    def expire(self, now: datetime, context: TransitionContext) -> int:
        """Purge every conversation past its fixed retention deadline."""
        rows = self._connection.execute(
            "SELECT conversation_id FROM relay_retention WHERE expires_at<=? ORDER BY expires_at",
            (now.isoformat(),),
        ).fetchall()
        for row in rows:
            _ = self._delete(UUID(str(row[0])), context)
        return len(rows)

    def _delete(self, conversation_id: UUID, context: TransitionContext) -> int:
        with self._connection:
            _ = self._connection.execute("BEGIN IMMEDIATE")
            event_rows = self._connection.execute(
                """SELECT record_id FROM canonical_conversation_events
                WHERE json_extract(content_json, '$.conversation_id')=?""",
                (str(conversation_id),),
            ).fetchall()
            _ = self._connection.execute(
                "DELETE FROM relay_events WHERE conversation_id=?", (str(conversation_id),)
            )
            self._authorize_delete()
            try:
                for row in event_rows:
                    _ = self._connection.execute(
                        "DELETE FROM canonical_conversation_events WHERE record_id=?",
                        (str(row[0]),),
                    )
                _ = self._connection.execute(
                    "DELETE FROM canonical_conversations WHERE record_id=?",
                    (str(conversation_id),),
                )
            finally:
                self._revoke_delete()
            _ = self._connection.execute(
                "DELETE FROM relay_retention WHERE conversation_id=?", (str(conversation_id),)
            )
            _ = self._connection.execute(
                "DELETE FROM relay_conversations WHERE conversation_id=?", (str(conversation_id),)
            )
            self._audit(
                conversation_id,
                "conversation.deleted",
                f"{conversation_id}:{len(event_rows)}",
                context,
            )
            return len(event_rows)

    def conversation(self, conversation_id: UUID, device_id: UUID) -> Conversation:
        """Authorize membership before returning conversation metadata."""
        device = self.device(device_id)
        row = self._connection.execute(
            """SELECT c.content_json FROM canonical_conversations c
            JOIN relay_conversations r ON r.conversation_id=c.record_id
            WHERE c.record_id=? ORDER BY c.version DESC LIMIT 1""",
            (str(conversation_id),),
        ).fetchone()
        if device is None or row is None:
            raise RelayAccessError
        record = ConversationRecord.model_validate_json(str(row[0]))
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

    def agent_history(
        self, conversation_id: UUID, device_id: UUID
    ) -> tuple[int, list[tuple[str, str]]]:
        """Return the last four exchanges; canonical content follows conversation retention."""
        conversation = self.conversation(conversation_id, device_id)
        if conversation.state is not ConversationState.ACTIVE:
            raise RelayConflictError
        rows = self._connection.execute(
            """SELECT content_json FROM canonical_conversation_events
            WHERE json_extract(content_json, '$.conversation_id')=?
            AND json_extract(content_json, '$.event_type')='agent.turn'
            ORDER BY json_extract(content_json, '$.sequence') DESC LIMIT 4""",
            (str(conversation_id),),
        ).fetchall()
        records = [CanonicalEvent.model_validate_json(str(row[0])) for row in rows]
        return (records[0].sequence if records else 0), [
            (record.payload.split("\n", 1)[0], record.payload.split("\n", 1)[1])
            for record in reversed(records)
        ]

    def save_agent_turn(self, request: AgentTurnPersistence) -> None:
        """Reject concurrent stale inference and audit only a content fingerprint."""
        with self._connection:
            _ = self._connection.execute("BEGIN IMMEDIATE")
            revision, _ = self.agent_history(request.conversation_id, request.device_id)
            if revision != request.expected_revision or revision >= MAX_CONVERSATION_EVENTS:
                raise RelayConflictError
            record = CanonicalEvent(
                record_id=RecordId(uuid4()),
                created_at=request.context.occurred_at,
                state="accepted",
                conversation_id=RecordId(request.conversation_id),
                sequence=revision + 1,
                event_type="agent.turn",
                payload=request.text.replace("\n", " ") + "\n" + request.reply,
            )
            _ = self._connection.execute(
                "INSERT INTO canonical_conversation_events VALUES (?, 1, ?, ?, ?)",
                (
                    str(record.record_id),
                    record.state,
                    record.model_dump_json(),
                    record.created_at.isoformat(),
                ),
            )
            self._audit(
                record.record_id,
                "conversation.agent.accepted",
                record.payload,
                request.context,
            )

    def _devices_for(self, participant_ids: tuple[RecordId, ...]) -> list[ConversationDevice]:
        rows = self._connection.execute("SELECT record_json FROM relay_devices").fetchall()
        devices = [ConversationDevice.model_validate_json(str(row[0])) for row in rows]
        return [device for device in devices if device.participant_id in participant_ids]

    def events(
        self, conversation_id: UUID, device_id: UUID, *, after: int = 0, limit: int = 100
    ) -> list[ConversationEvent]:
        """Read an authorized, ordered, bounded page; the cursor is the last sequence."""
        _ = self.conversation(conversation_id, device_id)
        if after < 0 or not 1 <= limit <= MAX_CONVERSATION_EVENTS:
            raise RelayConflictError
        rows = self._connection.execute(
            """SELECT c.content_json FROM relay_events r
            JOIN canonical_conversation_events c ON c.record_id=r.event_id AND c.version=1
            WHERE r.conversation_id=? AND r.sequence>? ORDER BY r.sequence LIMIT ?""",
            (str(conversation_id), after, limit),
        ).fetchall()
        return [
            ConversationEvent.model_validate_json(
                CanonicalEvent.model_validate_json(str(row[0])).payload
            )
            for row in rows
        ]

    def append(
        self,
        conversation_id: UUID,
        device_id: UUID,
        events: list[ConversationEvent],
        context: TransitionContext,
    ) -> int:
        """Atomically accept a batch; exact retries ACK without duplicating content or audit."""
        with self._connection:
            _ = self._connection.execute("BEGIN IMMEDIATE")
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
            if not 1 <= len(events) <= MAX_BATCH_EVENTS or len(
                {event.event_id for event in events}
            ) != len(events):
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
            "INSERT INTO canonical_conversation_events VALUES (?, 1, ?, ?, ?)",
            (
                str(record.record_id),
                record.state,
                record.model_dump_json(),
                record.created_at.isoformat(),
            ),
        )
        _ = self._connection.execute(
            "INSERT INTO relay_events VALUES (?, ?, ?)",
            (str(event.event_id), str(event.conversation_id), event.sequence),
        )
        self._audit(event.event_id, "conversation.event.accepted", record.payload, context)

    def _audit(
        self, record_id: UUID, action: str, content: str, context: TransitionContext
    ) -> None:
        _ = self._ledger.verify()
        self._ledger.append(
            AuditMutation(
                record_id=RecordId(record_id),
                record_kind=RecordKind.EVENT,
                state="accepted",
                action_class=action,
                source_fingerprint=hmac.digest(
                    self._keys.audit_key(), content.encode(), "sha256"
                ).hex(),
                context=context,
            )
        )
