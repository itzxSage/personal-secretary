"""Contract tests for the portable Conversation / Interface API.

The canonical conversation schema lives at ``contracts/v1/conversation-api.schema.json``
and the generated Python/Swift bindings are deterministic outputs of
``scripts/generate_contracts.py``. Adapters may only map conversation traffic through
these bindings; the ordering journal rejects duplicate, out-of-order, and foreign-user
events before any adapter can observe them.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar, Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from secretary_service.contract_version import CONTRACT_VERSION
from secretary_service.conversation_api import (
    Attachment,
    Cancellation,
    ChannelBinding,
    ChannelBindingState,
    ChannelKind,
    Conversation,
    ConversationAcceptance,
    ConversationDevice,
    ConversationError,
    ConversationEvent,
    ConversationEventKind,
    ConversationEventPayload,
    ConversationJournal,
    ConversationParticipant,
    ConversationRejection,
    ConversationState,
    DeviceKind,
    ParticipantKind,
    Resume,
    ToolResult,
    ToolResultStatus,
    TranscriptFinal,
    TranscriptPartial,
)

ROOT: Final = Path(__file__).resolve().parents[1]
TRANSPORT_SCHEMA: Final = ROOT / "contracts" / "v1" / "secretary-api.schema.json"
CONVERSATION_SCHEMA: Final = ROOT / "contracts" / "v1" / "conversation-api.schema.json"
GOLDEN_FIXTURE: Final = ROOT / "contracts" / "v1" / "fixtures" / "conversation-contract.json"
SWIFT_VERSION: Final = (
    ROOT / "ios" / "SecretaryApp" / "SecretaryApp" / "Contract" / "ContractVersion.generated.swift"
)
EXCHANGE_DIR: Final = ROOT / "artifacts" / "verification" / "task-4" / "exchange"

EXISTING_TRANSPORT_DEFS: Final = frozenset(
    {
        "health",
        "contract_error",
        "provider_request",
        "provider_response",
        "transition_context",
        "audit_entry",
        "redacted_record",
    }
)

USER_ID: Final = UUID("11111111-1111-4111-8111-111111111111")
AGENT_ID: Final = UUID("22222222-2222-4222-8222-222222222222")
IPHONE_ID: Final = UUID("33333333-3333-4333-8333-333333333333")
MACBOOK_ID: Final = UUID("44444444-4444-4444-8444-444444444444")
CONVERSATION_ID: Final = UUID("55555555-5555-4555-8555-555555555555")
FOREIGN_USER_ID: Final = UUID("99999999-9999-4999-8999-999999999999")
FOREIGN_DEVICE_ID: Final = UUID("88888888-8888-4888-8888-888888888888")
TURN_ONE: Final = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
TURN_TWO: Final = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
TURN_THREE: Final = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
BINDING_ID: Final = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
ATTACHMENT_ID: Final = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")

START: Final = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


class VersionDefinition(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    const: str


class ContractProperties(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    contract_version: VersionDefinition


class ContractSchema(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    properties: ContractProperties
    defs: dict[str, object] = Field(alias="$defs")


class ExpectedResult(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    accepted: bool
    rejection: ConversationRejection | None
    next_sequence: int


class ExchangeCase(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    name: str
    events: list[ConversationEvent]
    expected: list[ExpectedResult]


class ExchangeDocument(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    contract_version: str
    conversation: Conversation
    devices: list[ConversationDevice]
    participants: list[ConversationParticipant]
    channel_binding: ChannelBinding
    cases: list[ExchangeCase]


def event_id_for(sequence: int) -> UUID:
    """Return the deterministic event identifier for a sequence position."""
    return UUID(f"66666666-6666-4666-8666-{sequence:012d}")


def build_conversation_fixture() -> tuple[
    Conversation, list[ConversationDevice], list[ConversationParticipant]
]:
    """Build the canonical cross-device conversation used by every case."""
    user = ConversationParticipant(
        participant_id=USER_ID,
        kind=ParticipantKind.USER,
        display_name="Jared",
        created_at=START,
    )
    agent = ConversationParticipant(
        participant_id=AGENT_ID,
        kind=ParticipantKind.AGENT,
        display_name="LifeOS",
        created_at=START,
    )
    iphone = ConversationDevice(
        device_id=IPHONE_ID,
        participant_id=USER_ID,
        kind=DeviceKind.IOS,
        name="iPhone",
        created_at=START,
    )
    macbook = ConversationDevice(
        device_id=MACBOOK_ID,
        participant_id=USER_ID,
        kind=DeviceKind.MAC,
        name="MacBook",
        created_at=START,
    )
    conversation = Conversation(
        conversation_id=CONVERSATION_ID,
        title="Morning planning",
        participant_ids=[USER_ID, AGENT_ID],
        device_ids=[IPHONE_ID, MACBOOK_ID],
        created_at=START,
        state=ConversationState.ACTIVE,
    )
    return conversation, [iphone, macbook], [user, agent]


def build_binding() -> ChannelBinding:
    """Build the canonical channel binding for the fixture conversation."""
    return ChannelBinding(
        binding_id=BINDING_ID,
        conversation_id=CONVERSATION_ID,
        channel_kind=ChannelKind.TELEGRAM,
        external_channel_id="telegram-channel-42",
        external_user_id="telegram-user-7",
        bound_at=START,
        state=ChannelBindingState.ACTIVE,
    )


def build_event(
    *,
    sequence: int,
    kind: ConversationEventKind,
    payload: ConversationEventPayload,
    participant_id: UUID = USER_ID,
    device_id: UUID = IPHONE_ID,
) -> ConversationEvent:
    """Build one deterministic ordered conversation event."""
    return ConversationEvent(
        event_id=event_id_for(sequence),
        conversation_id=CONVERSATION_ID,
        participant_id=participant_id,
        device_id=device_id,
        sequence=sequence,
        occurred_at=START + timedelta(seconds=sequence),
        kind=kind,
        payload=payload,
    )


def partial_payload(turn_id: UUID, text: str) -> TranscriptPartial:
    """Build a partial transcript payload for a turn."""
    return TranscriptPartial(turn_id=turn_id, text=text, is_final=False)


def final_payload(turn_id: UUID, text: str) -> TranscriptFinal:
    """Build a final transcript payload for a turn."""
    return TranscriptFinal(turn_id=turn_id, text=text, is_final=True)


def build_exchange_document() -> ExchangeDocument:
    """Build the canonical cross-language exchange document."""
    conversation, devices, participants = build_conversation_fixture()

    def run_case(name: str, events: list[ConversationEvent]) -> ExchangeCase:
        journal = ConversationJournal(conversation=conversation, devices=devices)
        results = [journal.accept(event) for event in events]
        return ExchangeCase(
            name=name,
            events=events,
            expected=[
                ExpectedResult(
                    accepted=result.accepted,
                    rejection=result.rejection,
                    next_sequence=result.next_sequence,
                )
                for result in results
            ],
        )

    cases = [
        run_case(
            "accepted_cross_device",
            [
                build_event(
                    sequence=1,
                    kind=ConversationEventKind.TRANSCRIPT_PARTIAL,
                    payload=partial_payload(TURN_ONE, "what's left"),
                    device_id=IPHONE_ID,
                ),
                build_event(
                    sequence=2,
                    kind=ConversationEventKind.TRANSCRIPT_PARTIAL,
                    payload=partial_payload(TURN_ONE, "what's left today"),
                    device_id=MACBOOK_ID,
                ),
                build_event(
                    sequence=3,
                    kind=ConversationEventKind.TRANSCRIPT_FINAL,
                    payload=final_payload(TURN_ONE, "what's left today"),
                    device_id=IPHONE_ID,
                ),
                build_event(
                    sequence=4,
                    kind=ConversationEventKind.TRANSCRIPT_PARTIAL,
                    payload=partial_payload(TURN_TWO, "move workout"),
                    device_id=MACBOOK_ID,
                ),
                build_event(
                    sequence=5,
                    kind=ConversationEventKind.TRANSCRIPT_FINAL,
                    payload=final_payload(TURN_TWO, "move workout to 6pm"),
                    device_id=MACBOOK_ID,
                ),
            ],
        ),
        run_case(
            "rejected_duplicate",
            [
                build_event(
                    sequence=1,
                    kind=ConversationEventKind.TRANSCRIPT_PARTIAL,
                    payload=partial_payload(TURN_ONE, "hello"),
                ),
                build_event(
                    sequence=1,
                    kind=ConversationEventKind.TRANSCRIPT_PARTIAL,
                    payload=partial_payload(TURN_ONE, "hello"),
                ),
            ],
        ),
        run_case(
            "rejected_out_of_order",
            [
                build_event(
                    sequence=1,
                    kind=ConversationEventKind.TRANSCRIPT_PARTIAL,
                    payload=partial_payload(TURN_ONE, "one"),
                ),
                build_event(
                    sequence=3,
                    kind=ConversationEventKind.TRANSCRIPT_PARTIAL,
                    payload=partial_payload(TURN_ONE, "three"),
                ),
                build_event(
                    sequence=2,
                    kind=ConversationEventKind.TRANSCRIPT_PARTIAL,
                    payload=partial_payload(TURN_ONE, "two"),
                ),
                build_event(
                    sequence=2,
                    kind=ConversationEventKind.TRANSCRIPT_PARTIAL,
                    payload=partial_payload(TURN_ONE, "two again"),
                ),
            ],
        ),
        run_case(
            "rejected_foreign_participant",
            [
                build_event(
                    sequence=1,
                    kind=ConversationEventKind.TRANSCRIPT_PARTIAL,
                    payload=partial_payload(TURN_ONE, "intruder"),
                    participant_id=FOREIGN_USER_ID,
                ),
            ],
        ),
        run_case(
            "rejected_foreign_device",
            [
                build_event(
                    sequence=1,
                    kind=ConversationEventKind.TRANSCRIPT_PARTIAL,
                    payload=partial_payload(TURN_ONE, "stray device"),
                    device_id=FOREIGN_DEVICE_ID,
                ),
            ],
        ),
        run_case(
            "rejected_device_participant_mismatch",
            [
                build_event(
                    sequence=1,
                    kind=ConversationEventKind.TRANSCRIPT_PARTIAL,
                    payload=partial_payload(TURN_ONE, "agent on user phone"),
                    participant_id=AGENT_ID,
                    device_id=IPHONE_ID,
                ),
            ],
        ),
        run_case(
            "rejected_kind_mismatch",
            [
                build_event(
                    sequence=1,
                    kind=ConversationEventKind.TRANSCRIPT_FINAL,
                    payload=partial_payload(TURN_ONE, "envelope lies"),
                ),
            ],
        ),
        run_case(
            "rejected_final_without_partial",
            [
                build_event(
                    sequence=1,
                    kind=ConversationEventKind.TRANSCRIPT_FINAL,
                    payload=final_payload(TURN_ONE, "final without partial"),
                ),
            ],
        ),
        run_case(
            "rejected_event_after_cancellation",
            [
                build_event(
                    sequence=1,
                    kind=ConversationEventKind.TRANSCRIPT_PARTIAL,
                    payload=partial_payload(TURN_ONE, "start"),
                ),
                build_event(
                    sequence=2,
                    kind=ConversationEventKind.CANCELLATION,
                    payload=Cancellation(turn_id=TURN_ONE, reason="barge-in"),
                ),
                build_event(
                    sequence=3,
                    kind=ConversationEventKind.TRANSCRIPT_PARTIAL,
                    payload=partial_payload(TURN_ONE, "after cancel"),
                ),
            ],
        ),
        run_case(
            "rejected_resume_without_cancellation",
            [
                build_event(
                    sequence=1,
                    kind=ConversationEventKind.RESUME,
                    payload=Resume(turn_id=TURN_TWO, resume_from_turn_id=TURN_ONE),
                ),
            ],
        ),
        run_case(
            "accepted_cancel_resume",
            [
                build_event(
                    sequence=1,
                    kind=ConversationEventKind.TRANSCRIPT_PARTIAL,
                    payload=partial_payload(TURN_ONE, "start"),
                ),
                build_event(
                    sequence=2,
                    kind=ConversationEventKind.CANCELLATION,
                    payload=Cancellation(turn_id=TURN_ONE, reason="barge-in"),
                ),
                build_event(
                    sequence=3,
                    kind=ConversationEventKind.RESUME,
                    payload=Resume(turn_id=TURN_TWO, resume_from_turn_id=TURN_ONE),
                ),
                build_event(
                    sequence=4,
                    kind=ConversationEventKind.TRANSCRIPT_PARTIAL,
                    payload=partial_payload(TURN_TWO, "resumed"),
                ),
                build_event(
                    sequence=5,
                    kind=ConversationEventKind.TRANSCRIPT_FINAL,
                    payload=final_payload(TURN_TWO, "resumed and final"),
                ),
            ],
        ),
        run_case(
            "accepted_mixed_payloads",
            [
                build_event(
                    sequence=1,
                    kind=ConversationEventKind.ATTACHMENT,
                    payload=Attachment(
                        attachment_id=ATTACHMENT_ID,
                        mime_type="image/png",
                        size_bytes=2048,
                        reference="lifeos://attachment/eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
                    ),
                ),
                build_event(
                    sequence=2,
                    kind=ConversationEventKind.TOOL_RESULT,
                    payload=ToolResult(
                        tool_call_id="tool-call-1",
                        tool_name="calendar_propose",
                        status=ToolResultStatus.OK,
                        result_reference="lifeos://proposal/55555555-5555-4555-8555-555555555555",
                    ),
                ),
                build_event(
                    sequence=3,
                    kind=ConversationEventKind.ERROR,
                    payload=ConversationError(
                        code="provider_unavailable",
                        message="realtime provider unreachable",
                        retryable=True,
                    ),
                ),
            ],
        ),
    ]
    return ExchangeDocument(
        contract_version=CONTRACT_VERSION,
        conversation=conversation,
        devices=devices,
        participants=participants,
        channel_binding=build_binding(),
        cases=cases,
    )


def test_baseline_pins_existing_cross_language_contract_behavior() -> None:
    # Given: the versioned transport schema that predates the conversation API.
    schema = ContractSchema.model_validate_json(TRANSPORT_SCHEMA.read_bytes())

    # Then: the transport contract version is unchanged and shared by both languages.
    assert schema.properties.contract_version.const == "1.0.0"
    assert CONTRACT_VERSION == "1.0.0"

    # And: the Swift binding still exposes the same version token.
    generated_source = SWIFT_VERSION.read_text(encoding="utf-8")
    assert 'rawValue = "1.0.0"' in generated_source

    # And: the transport schema keeps exactly its pinned definition set.
    assert set(schema.defs) == set(EXISTING_TRANSPORT_DEFS)


def test_conversation_schema_is_versioned_identically() -> None:
    # Given: the canonical conversation schema.
    schema = ContractSchema.model_validate_json(CONVERSATION_SCHEMA.read_bytes())

    # Then: it shares the transport contract generation.
    assert schema.properties.contract_version.const == CONTRACT_VERSION


def test_accepts_cross_device_conversation_events() -> None:
    # Given: a conversation bound to two devices of the same participant.
    conversation, devices, _ = build_conversation_fixture()
    journal = ConversationJournal(conversation=conversation, devices=devices)

    # When: interleaved events from both devices arrive in sequence order.
    results = [
        journal.accept(
            build_event(
                sequence=1,
                kind=ConversationEventKind.TRANSCRIPT_PARTIAL,
                payload=partial_payload(TURN_ONE, "what's left"),
                device_id=IPHONE_ID,
            )
        ),
        journal.accept(
            build_event(
                sequence=2,
                kind=ConversationEventKind.TRANSCRIPT_PARTIAL,
                payload=partial_payload(TURN_ONE, "what's left today"),
                device_id=MACBOOK_ID,
            )
        ),
        journal.accept(
            build_event(
                sequence=3,
                kind=ConversationEventKind.TRANSCRIPT_FINAL,
                payload=final_payload(TURN_ONE, "what's left today"),
                device_id=IPHONE_ID,
            )
        ),
    ]

    # Then: every event is accepted and the sequence advances monotonically.
    assert [result.accepted for result in results] == [True, True, True]
    assert [result.next_sequence for result in results] == [2, 3, 4]
    assert all(result.rejection is None for result in results)


def test_rejects_duplicate_event() -> None:
    # Given: a journal that already accepted an event.
    conversation, devices, _ = build_conversation_fixture()
    journal = ConversationJournal(conversation=conversation, devices=devices)
    event = build_event(
        sequence=1,
        kind=ConversationEventKind.TRANSCRIPT_PARTIAL,
        payload=partial_payload(TURN_ONE, "hello"),
    )
    assert journal.accept(event).accepted

    # When: the identical event is delivered again.
    result = journal.accept(event)

    # Then: the duplicate is rejected with a typed reason and no sequence advance.
    assert result.accepted is False
    assert result.rejection == ConversationRejection.DUPLICATE_EVENT
    assert result.next_sequence == 2


def test_rejects_out_of_order_event() -> None:
    # Given: a journal expecting sequence 2.
    conversation, devices, _ = build_conversation_fixture()
    journal = ConversationJournal(conversation=conversation, devices=devices)
    _ = journal.accept(
        build_event(
            sequence=1,
            kind=ConversationEventKind.TRANSCRIPT_PARTIAL,
            payload=partial_payload(TURN_ONE, "one"),
        )
    )

    # When: an event with a sequence gap arrives.
    gap = journal.accept(
        build_event(
            sequence=3,
            kind=ConversationEventKind.TRANSCRIPT_PARTIAL,
            payload=partial_payload(TURN_ONE, "three"),
        )
    )

    # Then: the gap is rejected and the expected sequence is preserved.
    assert gap.accepted is False
    assert gap.rejection == ConversationRejection.OUT_OF_ORDER
    assert gap.next_sequence == 2

    # And: the missing event can still be accepted afterwards.
    recovery = journal.accept(
        build_event(
            sequence=2,
            kind=ConversationEventKind.TRANSCRIPT_PARTIAL,
            payload=partial_payload(TURN_ONE, "two"),
        )
    )
    assert recovery.accepted
    assert recovery.next_sequence == 3


def test_rejects_foreign_participant_event() -> None:
    # Given: a conversation that does not include a foreign participant.
    conversation, devices, _ = build_conversation_fixture()
    journal = ConversationJournal(conversation=conversation, devices=devices)

    # When: the foreign participant sends an event.
    result = journal.accept(
        build_event(
            sequence=1,
            kind=ConversationEventKind.TRANSCRIPT_PARTIAL,
            payload=partial_payload(TURN_ONE, "intruder"),
            participant_id=FOREIGN_USER_ID,
        )
    )

    # Then: the foreign-user event is rejected without advancing the sequence.
    assert result.accepted is False
    assert result.rejection == ConversationRejection.FOREIGN_PARTICIPANT
    assert result.next_sequence == 1


def test_rejects_foreign_device_event() -> None:
    # Given: a conversation that does not include a foreign device.
    conversation, devices, _ = build_conversation_fixture()
    journal = ConversationJournal(conversation=conversation, devices=devices)

    # When: an unbound device sends an event.
    result = journal.accept(
        build_event(
            sequence=1,
            kind=ConversationEventKind.TRANSCRIPT_PARTIAL,
            payload=partial_payload(TURN_ONE, "stray device"),
            device_id=FOREIGN_DEVICE_ID,
        )
    )

    # Then: the foreign-device event is rejected.
    assert result.accepted is False
    assert result.rejection == ConversationRejection.FOREIGN_DEVICE
    assert result.next_sequence == 1


def test_rejects_device_participant_mismatch() -> None:
    # Given: a conversation where the agent is a participant but owns no device.
    conversation, devices, _ = build_conversation_fixture()
    journal = ConversationJournal(conversation=conversation, devices=devices)

    # When: the agent sends an event through the user's iPhone.
    result = journal.accept(
        build_event(
            sequence=1,
            kind=ConversationEventKind.TRANSCRIPT_PARTIAL,
            payload=partial_payload(TURN_ONE, "agent on user phone"),
            participant_id=AGENT_ID,
            device_id=IPHONE_ID,
        )
    )

    # Then: the device/participant binding mismatch is rejected.
    assert result.accepted is False
    assert result.rejection == ConversationRejection.DEVICE_PARTICIPANT_MISMATCH
    assert result.next_sequence == 1


def test_rejects_kind_mismatch() -> None:
    # Given: a journal expecting a transcript event.
    conversation, devices, _ = build_conversation_fixture()
    journal = ConversationJournal(conversation=conversation, devices=devices)

    # When: the envelope kind contradicts the payload kind.
    result = journal.accept(
        build_event(
            sequence=1,
            kind=ConversationEventKind.TRANSCRIPT_FINAL,
            payload=partial_payload(TURN_ONE, "envelope lies"),
        )
    )

    # Then: the contradictory event is rejected.
    assert result.accepted is False
    assert result.rejection == ConversationRejection.KIND_MISMATCH
    assert result.next_sequence == 1


def test_rejects_final_without_partial() -> None:
    # Given: a journal with no open transcript turn.
    conversation, devices, _ = build_conversation_fixture()
    journal = ConversationJournal(conversation=conversation, devices=devices)

    # When: a final transcript arrives without any preceding partial.
    result = journal.accept(
        build_event(
            sequence=1,
            kind=ConversationEventKind.TRANSCRIPT_FINAL,
            payload=final_payload(TURN_ONE, "final without partial"),
        )
    )

    # Then: the orphan final is rejected.
    assert result.accepted is False
    assert result.rejection == ConversationRejection.FINAL_WITHOUT_PARTIAL
    assert result.next_sequence == 1


def test_rejects_event_after_cancellation() -> None:
    # Given: a turn that was cancelled by barge-in.
    conversation, devices, _ = build_conversation_fixture()
    journal = ConversationJournal(conversation=conversation, devices=devices)
    _ = journal.accept(
        build_event(
            sequence=1,
            kind=ConversationEventKind.TRANSCRIPT_PARTIAL,
            payload=partial_payload(TURN_ONE, "start"),
        )
    )
    _ = journal.accept(
        build_event(
            sequence=2,
            kind=ConversationEventKind.CANCELLATION,
            payload=Cancellation(turn_id=TURN_ONE, reason="barge-in"),
        )
    )

    # When: the cancelled turn produces another partial.
    result = journal.accept(
        build_event(
            sequence=3,
            kind=ConversationEventKind.TRANSCRIPT_PARTIAL,
            payload=partial_payload(TURN_ONE, "after cancel"),
        )
    )

    # Then: the cancelled turn is closed to further events.
    assert result.accepted is False
    assert result.rejection == ConversationRejection.EVENT_AFTER_CANCELLATION
    assert result.next_sequence == 3


def test_rejects_resume_without_cancellation() -> None:
    # Given: a journal with no cancelled turn to resume from.
    conversation, devices, _ = build_conversation_fixture()
    journal = ConversationJournal(conversation=conversation, devices=devices)

    # When: a resume references a turn that was never cancelled.
    result = journal.accept(
        build_event(
            sequence=1,
            kind=ConversationEventKind.RESUME,
            payload=Resume(turn_id=TURN_TWO, resume_from_turn_id=TURN_ONE),
        )
    )

    # Then: the orphan resume is rejected.
    assert result.accepted is False
    assert result.rejection == ConversationRejection.RESUME_WITHOUT_CANCELLATION
    assert result.next_sequence == 1


def test_accepts_cancellation_then_resume() -> None:
    # Given: a conversation journal.
    conversation, devices, _ = build_conversation_fixture()
    journal = ConversationJournal(conversation=conversation, devices=devices)

    # When: a turn is cancelled and a new turn resumes from it.
    results = [
        journal.accept(
            build_event(
                sequence=1,
                kind=ConversationEventKind.TRANSCRIPT_PARTIAL,
                payload=partial_payload(TURN_ONE, "start"),
            )
        ),
        journal.accept(
            build_event(
                sequence=2,
                kind=ConversationEventKind.CANCELLATION,
                payload=Cancellation(turn_id=TURN_ONE, reason="barge-in"),
            )
        ),
        journal.accept(
            build_event(
                sequence=3,
                kind=ConversationEventKind.RESUME,
                payload=Resume(turn_id=TURN_TWO, resume_from_turn_id=TURN_ONE),
            )
        ),
        journal.accept(
            build_event(
                sequence=4,
                kind=ConversationEventKind.TRANSCRIPT_PARTIAL,
                payload=partial_payload(TURN_TWO, "resumed"),
            )
        ),
        journal.accept(
            build_event(
                sequence=5,
                kind=ConversationEventKind.TRANSCRIPT_FINAL,
                payload=final_payload(TURN_TWO, "resumed and final"),
            )
        ),
    ]

    # Then: cancellation closes the old turn and resume opens a new one.
    assert [result.accepted for result in results] == [True, True, True, True, True]
    assert [result.next_sequence for result in results] == [2, 3, 4, 5, 6]


def test_accepts_attachment_tool_result_and_error_events() -> None:
    # Given: a conversation journal.
    conversation, devices, _ = build_conversation_fixture()
    journal = ConversationJournal(conversation=conversation, devices=devices)

    # When: attachment, tool-result, and error events arrive in order.
    results = [
        journal.accept(
            build_event(
                sequence=1,
                kind=ConversationEventKind.ATTACHMENT,
                payload=Attachment(
                    attachment_id=ATTACHMENT_ID,
                    mime_type="image/png",
                    size_bytes=2048,
                    reference="lifeos://attachment/eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
                ),
            )
        ),
        journal.accept(
            build_event(
                sequence=2,
                kind=ConversationEventKind.TOOL_RESULT,
                payload=ToolResult(
                    tool_call_id="tool-call-1",
                    tool_name="calendar_propose",
                    status=ToolResultStatus.OK,
                    result_reference="lifeos://proposal/55555555-5555-4555-8555-555555555555",
                ),
            )
        ),
        journal.accept(
            build_event(
                sequence=3,
                kind=ConversationEventKind.ERROR,
                payload=ConversationError(
                    code="provider_unavailable",
                    message="realtime provider unreachable",
                    retryable=True,
                ),
            )
        ),
    ]

    # Then: all three payload kinds are accepted in sequence.
    assert [result.accepted for result in results] == [True, True, True]
    assert [result.next_sequence for result in results] == [2, 3, 4]


def test_channel_binding_round_trips_through_api() -> None:
    # Given: a channel binding constructed through the API.
    binding = build_binding()

    # When: it is serialized and validated back.
    serialized = binding.model_dump_json()
    restored = ChannelBinding.model_validate_json(serialized)

    # Then: the binding survives the round trip unchanged.
    assert restored == binding
    assert restored.channel_kind == ChannelKind.TELEGRAM
    assert restored.state == ChannelBindingState.ACTIVE


def test_exchange_document_matches_canonical_fixture_and_writes_live_artifact() -> None:
    # Given: the canonical exchange document serialized by the Python SDK.
    document = build_exchange_document()
    serialized = document.model_dump_json(indent=2) + "\n"

    # Then: the bytes match the versioned canonical fixture exactly.
    assert serialized == GOLDEN_FIXTURE.read_text(encoding="utf-8")

    # And: the live exchange artifact is written for the Swift side to consume.
    EXCHANGE_DIR.mkdir(parents=True, exist_ok=True)
    _ = (EXCHANGE_DIR / "conversation-contract.json").write_text(serialized, encoding="utf-8")


def test_exchange_document_cases_match_expected_outcomes() -> None:
    # Given: the canonical exchange document.
    document = build_exchange_document()

    # When: every case is replayed through a fresh journal.
    for case in document.cases:
        journal = ConversationJournal(
            conversation=document.conversation,
            devices=document.devices,
        )
        results = [journal.accept(event) for event in case.events]

        # Then: each step matches the pinned expected outcome.
        assert len(results) == len(case.expected)
        for result, expected in zip(results, case.expected, strict=True):
            assert result.accepted == expected.accepted, case.name
            assert result.rejection == expected.rejection, case.name
            assert result.next_sequence == expected.next_sequence, case.name


def test_rejected_events_never_advance_the_sequence() -> None:
    # Given: a journal that rejects a foreign-user event.
    conversation, devices, _ = build_conversation_fixture()
    journal = ConversationJournal(conversation=conversation, devices=devices)
    rejected = journal.accept(
        build_event(
            sequence=1,
            kind=ConversationEventKind.TRANSCRIPT_PARTIAL,
            payload=partial_payload(TURN_ONE, "intruder"),
            participant_id=FOREIGN_USER_ID,
        )
    )
    assert rejected.accepted is False

    # When: the legitimate participant then sends the same sequence position.
    accepted = journal.accept(
        build_event(
            sequence=1,
            kind=ConversationEventKind.TRANSCRIPT_PARTIAL,
            payload=partial_payload(TURN_ONE, "legitimate"),
        )
    )

    # Then: the rejected event did not consume the sequence slot.
    assert accepted.accepted
    assert accepted.next_sequence == 2


def test_conversation_acceptance_is_typed_and_serializable() -> None:
    # Given: a typed acceptance result.
    conversation, devices, _ = build_conversation_fixture()
    journal = ConversationJournal(conversation=conversation, devices=devices)
    result = journal.accept(
        build_event(
            sequence=1,
            kind=ConversationEventKind.TRANSCRIPT_PARTIAL,
            payload=partial_payload(TURN_ONE, "typed"),
        )
    )

    # When: it is serialized and validated back.
    restored = ConversationAcceptance.model_validate_json(result.model_dump_json())

    # Then: the typed result survives the round trip unchanged.
    assert restored == result
    assert restored.accepted is True
    assert restored.rejection is None
    assert restored.next_sequence == 2
