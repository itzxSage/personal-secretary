import Foundation
import SecretaryClient
import SecretaryContract
import Testing

private struct PermissionStub: MicrophonePermissionProviding {
    let authorization: MicrophoneAuthorization

    func requestAuthorization() async -> MicrophoneAuthorization {
        authorization
    }
}

private actor RecordingSink: ConversationEventSink {
    private(set) var events: [ConversationEvent] = []

    func send(_ event: ConversationEvent) async throws {
        events.append(event)
    }

    func recorded() -> [ConversationEvent] {
        events
    }
}

private struct EventCommandStub: ConversationCommand {
    let event: ConversationEvent
    let sink: any ConversationEventSink

    func execute(values: InvocationValues) async throws -> ConversationEvent {
        try await sink.send(event)
        return event
    }
}

private let identity = ConversationIdentity(
    conversationID: UUID(uuidString: "55555555-5555-4555-8555-555555555555")!,
    participantID: UUID(uuidString: "11111111-1111-4111-8111-111111111111")!,
    deviceID: UUID(uuidString: "33333333-3333-4333-8333-333333333333")!
)

private let turnID = UUID(uuidString: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")!

private func makeAdapter(sink: any ConversationEventSink) -> InvocationAdapter {
    let command = StartVoiceSession(
        identity: identity,
        permission: PermissionStub(authorization: .granted),
        sink: sink
    )
    return InvocationAdapter(startVoiceSession: command)
}

private func values(eventID: UUID, sequence: Int) -> InvocationValues {
    InvocationValues(
        eventID: eventID,
        turnID: turnID,
        occurredAt: Date(timeIntervalSince1970: 1_788_609_601),
        sequence: sequence
    )
}

private func cancellationEvent(eventID: UUID, sequence: Int) throws -> ConversationEvent {
    let data = Data(
        """
        {
          "event_id": "\(eventID.uuidString)",
          "conversation_id": "\(identity.conversationID.uuidString)",
          "participant_id": "\(identity.participantID.uuidString)",
          "device_id": "\(identity.deviceID.uuidString)",
          "sequence": \(sequence),
          "occurred_at": "2026-09-06T12:00:02Z",
          "kind": "cancellation",
          "payload": {
            "kind": "cancellation",
            "turn_id": "\(turnID.uuidString)",
            "reason": "barge_in"
          }
        }
        """.utf8
    )
    let decoder = JSONDecoder()
    decoder.dateDecodingStrategy = .lifeOSISO8601
    return try decoder.decode(ConversationEvent.self, from: data)
}

@Test("voice session start emits a monotonic partial transcript event")
func voiceStartEmitsOrderedPartial() async throws {
    // Given: a granted voice invocation with a fixed event identity.
    let sink = RecordingSink()
    let adapter = makeAdapter(sink: sink)
    let eventID = UUID(uuidString: "66666666-6666-4666-8666-000000000001")!

    // When: the shared adapter starts a voice session.
    let outcome = await adapter.invoke(.pushToTalk, values: values(eventID: eventID, sequence: 1))

    // Then: exactly one ordered partial transcript reaches the sink.
    guard case .started(let event) = outcome else {
        Issue.record("expected started outcome")
        return
    }
    #expect(event.eventId == eventID)
    #expect(event.sequence == 1)
    #expect(event.kind == .transcriptPartial)
    guard case .transcriptPartial(let partial) = event.payload else {
        Issue.record("expected transcript partial payload")
        return
    }
    #expect(partial.turnId == turnID)
    #expect(partial.isFinal == false)
    #expect(await sink.recorded() == [event])
}

@Test("distinct voice invocations keep strictly increasing sequence numbers")
func voiceInvocationsIncreaseSequenceMonotonically() async throws {
    // Given: two voice starts on the same conversation and device.
    let sink = RecordingSink()
    let adapter = makeAdapter(sink: sink)
    let firstID = UUID(uuidString: "66666666-6666-4666-8666-000000000001")!
    let secondID = UUID(uuidString: "66666666-6666-4666-8666-000000000002")!

    // When: both invocations route through the shared adapter.
    _ = await adapter.invoke(.pushToTalk, values: values(eventID: firstID, sequence: 1))
    _ = await adapter.invoke(.appShortcut, values: values(eventID: secondID, sequence: 2))

    // Then: the relay sees strictly increasing, non-duplicate event identities.
    let recorded = await sink.recorded()
    #expect(recorded.count == 2)
    #expect(recorded[0].eventId == firstID)
    #expect(recorded[1].eventId == secondID)
    #expect(recorded[0].sequence < recorded[1].sequence)
    #expect(Set(recorded.map(\.eventId)).count == 2)
}

@Test("cancellation routes through the shared command without a new transcript")
func cancellationRoutesThroughSharedCommand() async throws {
    // Given: a voice session that has already started.
    let sink = RecordingSink()
    let adapter = makeAdapter(sink: sink)
    let startID = UUID(uuidString: "66666666-6666-4666-8666-000000000001")!
    _ = await adapter.invoke(.pushToTalk, values: values(eventID: startID, sequence: 1))

    // When: a cancellation is delivered for the same turn through the adapter.
    let cancelID = UUID(uuidString: "66666666-6666-4666-8666-000000000002")!
    let cancellation = try cancellationEvent(eventID: cancelID, sequence: 2)
    let cancellationAdapter = InvocationAdapter(
        startVoiceSession: EventCommandStub(event: cancellation, sink: sink)
    )
    let outcome = await cancellationAdapter.invoke(
        .pushToTalk,
        values: values(eventID: cancelID, sequence: 2)
    )

    // Then: the cancellation follows the partial and no final transcript is emitted.
    let recorded = await sink.recorded()
    #expect(outcome == .started(cancellation))
    #expect(recorded.count == 2)
    #expect(recorded[0].kind == .transcriptPartial)
    #expect(recorded.last?.kind == .cancellation)
    #expect(recorded.map(\.sequence) == [1, 2])
    #expect(!recorded.contains { $0.kind == .transcriptFinal })
}

@Test("voice events never carry token or credential material")
func voiceEventsRedactTokensAndCredentials() async throws {
    // Given: a granted voice invocation.
    let sink = RecordingSink()
    let adapter = makeAdapter(sink: sink)
    let eventID = UUID(uuidString: "66666666-6666-4666-8666-000000000001")!

    // When: the adapter starts a voice session.
    let outcome = await adapter.invoke(.pushToTalk, values: values(eventID: eventID, sequence: 1))

    // Then: no token, signing key, or provider credential leaks into the event.
    guard case .started(let event) = outcome else {
        Issue.record("expected started outcome")
        return
    }
    let encoder = JSONEncoder()
    encoder.dateEncodingStrategy = .iso8601
    let data = try encoder.encode(event)
    let serialized = String(decoding: data, as: UTF8.self)
    #expect(!serialized.contains("token"))
    #expect(!serialized.contains("signing"))
    #expect(!serialized.contains("openai"))
    #expect(!serialized.contains("database-key"))
}
