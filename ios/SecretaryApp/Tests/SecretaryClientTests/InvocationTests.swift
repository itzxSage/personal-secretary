import CryptoKit
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

private let identity = ConversationIdentity(
    conversationID: UUID(uuidString: "55555555-5555-4555-8555-555555555555")!,
    participantID: UUID(uuidString: "11111111-1111-4111-8111-111111111111")!,
    deviceID: UUID(uuidString: "33333333-3333-4333-8333-333333333333")!
)

private let values = InvocationValues(
    eventID: UUID(uuidString: "66666666-6666-4666-8666-000000000001")!,
    turnID: UUID(uuidString: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")!,
    occurredAt: Date(timeIntervalSince1970: 1_788_609_601),
    sequence: 1
)

private func makeAdapter(
    permission: MicrophoneAuthorization = .granted,
    sink: any ConversationEventSink
) -> InvocationAdapter {
    let command = StartVoiceSession(
        identity: identity,
        permission: PermissionStub(authorization: permission),
        sink: sink
    )
    return InvocationAdapter(startVoiceSession: command)
}

@Test("all supported entry handlers create the same conversation event")
func sharedHandlersCreateSameEvent() async throws {
    var events: [ConversationEvent] = []

    for source in InvocationSource.systemSurfaces {
        let sink = RecordingSink()
        let adapter = makeAdapter(sink: sink)
        let handler = InvocationHandler(source: source, adapter: adapter)

        let outcome = await handler.handle(values: values)
        guard case .started(let event) = outcome else {
            Issue.record("expected started outcome for \(source.rawValue)")
            continue
        }
        events.append(event)
    }

    #expect(events.count == InvocationSource.systemSurfaces.count)
    #expect(events.dropFirst().allSatisfy { $0 == events.first })
}

@Test("microphone denial emits no event and offers text fallback")
func permissionDenialDegradesSafely() async {
    let sink = RecordingSink()
    let adapter = makeAdapter(permission: .denied, sink: sink)

    let outcome = await adapter.invoke(.pushToTalk, values: values)

    #expect(outcome == .permissionDenied(fallback: .textConversation))
    #expect(await sink.recorded().isEmpty)
}

@Test("undetermined microphone permission emits no event and offers text fallback")
func undeterminedPermissionDegradesSafely() async {
    // Given: a voice invocation whose permission request remains unresolved.
    let sink = RecordingSink()
    let adapter = makeAdapter(permission: .undetermined, sink: sink)

    // When: the shared adapter handles the invocation.
    let outcome = await adapter.invoke(.appShortcut, values: values)

    // Then: no event escapes and text remains available.
    #expect(outcome == .permissionDenied(fallback: .textConversation))
    #expect(await sink.recorded().isEmpty)
}

@Test("ending a voice session durably closes its active turn")
func endingVoiceSessionEmitsCancellation() async throws {
    let sink = RecordingSink()
    let command = EndVoiceSession(identity: identity, sink: sink)

    let event = try await command.execute(
        values: InvocationValues(
            eventID: UUID(uuidString: "66666666-6666-4666-8666-000000000002")!,
            turnID: values.turnID,
            occurredAt: values.occurredAt,
            sequence: 2
        )
    )

    #expect(event.kind == .cancellation)
    guard case .cancellation(let payload) = event.payload else {
        Issue.record("expected cancellation payload")
        return
    }
    #expect(payload.turnId == values.turnID)
    #expect(payload.reason == "user_ended_voice_session")
    #expect(await sink.recorded() == [event])
}

@Test("notification deep link reaches the shared invocation handler")
func notificationDeepLinkRoutesToInvocation() async throws {
    let sink = RecordingSink()
    let adapter = makeAdapter(sink: sink)
    let router = DeepLinkRouter(adapter: adapter)
    let url = try #require(URL(string: "lifeos://conversation/start?source=notification"))

    let outcome = await router.handle(url, values: values)

    guard case .started(let event) = outcome else {
        Issue.record("expected notification deep link to start")
        return
    }
    #expect(event.kind == .transcriptPartial)
    #expect(await sink.recorded() == [event])
}

@Test("unknown deep links fail closed")
func unknownDeepLinkFailsClosed() async throws {
    let router = DeepLinkRouter(adapter: makeAdapter(sink: RecordingSink()))
    let url = try #require(URL(string: "lifeos://conversation/delete"))

    #expect(await router.handle(url, values: values) == .invalidInvocation)
}

@Test("unknown deep-link sources fail closed")
func unknownDeepLinkSourceFailsClosed() async throws {
    // Given: a valid route carrying an unrecognized invocation source.
    let sink = RecordingSink()
    let router = DeepLinkRouter(adapter: makeAdapter(sink: sink))
    let url = try #require(URL(string: "lifeos://conversation/start?source=unknown_surface"))

    // When: the URL reaches the typed boundary.
    let outcome = await router.handle(url, values: values)

    // Then: the route is rejected without creating an event.
    #expect(outcome == .invalidInvocation)
    #expect(await sink.recorded().isEmpty)
}

@Test("encrypted outbox round trips without plaintext at rest")
func encryptedOutboxRoundTrip() async throws {
    let directory = FileManager.default.temporaryDirectory
        .appendingPathComponent(UUID().uuidString, isDirectory: true)
    defer { try? FileManager.default.removeItem(at: directory) }
    let key = SymmetricKey(data: Data(repeating: 0x2a, count: 32))
    let outbox = EncryptedOutbox(directory: directory, key: key)
    let adapter = makeAdapter(sink: outbox)

    _ = await adapter.invoke(.pushToTalk, values: values)
    let encrypted = try Data(contentsOf: outbox.fileURL)
    let pending = try await outbox.pendingEvents()

    #expect(!encrypted.contains(Data("voice_session_started".utf8)))
    #expect(pending.count == 1)
    #expect(pending[0].eventId == values.eventID)
}

@Test("surface matrix matches the supported iPhone versions")
func supportedSurfaceMatrix() {
    let base = IOSInvocationMatrix.supportedSurfaces(
        for: .init(osMajor: 17, osMinor: 6, model: .iPhone13, lockScreenEligible: true)
    )
    let pro17 = IOSInvocationMatrix.supportedSurfaces(
        for: .init(osMajor: 17, osMinor: 6, model: .iPhone15Pro, lockScreenEligible: true)
    )
    let pro18 = IOSInvocationMatrix.supportedSurfaces(
        for: .init(osMajor: 18, osMinor: 0, model: .iPhone15ProMax, lockScreenEligible: true)
    )

    #expect(base == [.pushToTalk, .appShortcut, .backTap, .lockScreen])
    #expect(pro17 == [.pushToTalk, .appShortcut, .backTap, .actionButton, .lockScreen])
    #expect(pro18 == [.pushToTalk, .appShortcut, .backTap, .actionButton, .controlCenter, .lockScreen])
    #expect(IOSInvocationMatrix.evidenceState(for: .actionButton) == .manualConfigurationRequired)
    #expect(IOSInvocationMatrix.evidenceState(for: .controlCenter) == .deviceRegistrationRequired)
    #expect(IOSInvocationMatrix.evidenceState(for: .lockScreen) == .deviceRegistrationRequired)
}
