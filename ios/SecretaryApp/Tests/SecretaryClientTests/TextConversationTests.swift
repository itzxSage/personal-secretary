import Foundation
import SecretaryClient
import SecretaryContract
import Testing

private actor TextRecordingSink: ConversationEventSink {
    private var events: [ConversationEvent] = []

    func send(_ event: ConversationEvent) async throws {
        events.append(event)
    }

    func recorded() -> [ConversationEvent] {
        events
    }
}

private let textIdentity = ConversationIdentity(
    conversationID: UUID(uuidString: "55555555-5555-4555-8555-555555555555")!,
    participantID: UUID(uuidString: "11111111-1111-4111-8111-111111111111")!,
    deviceID: UUID(uuidString: "33333333-3333-4333-8333-333333333333")!
)

@Test("text conversation emits an ordered partial and final through the Conversation API")
func textConversationEmitsOrderedEvents() async throws {
    // Given: a nonempty message and the shared conversation identity.
    let sink = TextRecordingSink()
    let command = SendTextMessage(identity: textIdentity, sink: sink)
    let request = try TextMessageRequest(
        text: "Plan tomorrow",
        identifiers: TextMessageIdentifiers(
            partialEventID: UUID(uuidString: "66666666-6666-4666-8666-000000000010")!,
            finalEventID: UUID(uuidString: "66666666-6666-4666-8666-000000000011")!,
            turnID: UUID(uuidString: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaab")!
        ),
        position: TextMessagePosition(
            occurredAt: Date(timeIntervalSince1970: 1_788_609_602),
            startingSequence: 4
        )
    )

    // When: the text command executes.
    let events = try await command.execute(request)

    // Then: the canonical event stream opens and closes one turn in order.
    #expect(events.map(\.sequence) == [4, 5])
    #expect(events.map(\.kind) == [.transcriptPartial, .transcriptFinal])
    #expect(await sink.recorded() == events)
    guard case .transcriptFinal(let final) = events[1].payload else {
        Issue.record("expected final transcript payload")
        return
    }
    #expect(final.text == "Plan tomorrow")
}

@Test("blank text is rejected before an event reaches the outbox")
func blankTextFailsAtBoundary() async {
    // Given: a text command and whitespace-only user input.
    let sink = TextRecordingSink()
    let command = SendTextMessage(identity: textIdentity, sink: sink)

    // When/Then: parsing rejects the input and no event is emitted.
    #expect(throws: TextMessageError.emptyMessage) {
        _ = try TextMessageRequest(
            text: "   ",
            identifiers: TextMessageIdentifiers(
                partialEventID: UUID(),
                finalEventID: UUID(),
                turnID: UUID()
            ),
            position: TextMessagePosition(
                occurredAt: Date(timeIntervalSince1970: 1_788_609_602),
                startingSequence: 1
            )
        )
    }
    #expect(await sink.recorded().isEmpty)
    _ = command
}

@Test("conversation UI identifiers expose text and voice controls")
func conversationUIIdentifiersAreStableAndUnique() {
    // Given: the identifiers consumed by simulator UI automation.
    let identifiers = ConversationAccessibilityID.allCases.map(\.rawValue)

    // When: the identifier catalog is inspected.
    let uniqueIdentifiers = Set(identifiers)

    // Then: every required conversation control has one stable unique identifier.
    #expect(uniqueIdentifiers.count == identifiers.count)
    #expect(ConversationAccessibilityID.requiredControls == [
        .shell,
        .transcript,
        .textField,
        .sendButton,
        .pushToTalkButton,
        .status,
    ])
}
