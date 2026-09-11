import CryptoKit
import Foundation
import SecretaryClient
import SecretaryContract
import Testing

private struct GrantedPermission: MicrophonePermissionProviding {
    func requestAuthorization() async -> MicrophoneAuthorization { .granted }
}

private let outboxIdentity = ConversationIdentity(
    conversationID: UUID(uuidString: "55555555-5555-4555-8555-555555555555")!,
    participantID: UUID(uuidString: "11111111-1111-4111-8111-111111111111")!,
    deviceID: UUID(uuidString: "33333333-3333-4333-8333-333333333333")!
)

@Test("concurrent offline writes retain every encrypted event")
func concurrentOutboxWritesAreSerialized() async throws {
    // Given: one encrypted outbox shared by concurrent invocations.
    let directory = FileManager.default.temporaryDirectory
        .appendingPathComponent(UUID().uuidString, isDirectory: true)
    defer { try? FileManager.default.removeItem(at: directory) }
    let outbox = EncryptedOutbox(
        directory: directory,
        key: SymmetricKey(data: Data(repeating: 0x31, count: 32))
    )
    let command = StartVoiceSession(identity: outboxIdentity, permission: GrantedPermission(), sink: outbox)

    // When: eight events are written at the same time.
    try await withThrowingTaskGroup(of: Void.self) { group in
        for sequence in 1...8 {
            group.addTask {
                _ = try await command.execute(values: InvocationValues(
                    eventID: UUID(),
                    turnID: UUID(),
                    occurredAt: Date(timeIntervalSince1970: TimeInterval(sequence)),
                    sequence: sequence
                ))
            }
        }
        try await group.waitForAll()
    }

    // Then: every event remains decryptable in the queue.
    #expect(try await outbox.pendingEvents().count == 8)
}

@Test("acknowledging delivered events removes only those ciphertext entries")
func acknowledgingOutboxEventsPreservesPendingEvents() async throws {
    // Given: two offline events in one encrypted outbox.
    let directory = FileManager.default.temporaryDirectory
        .appendingPathComponent(UUID().uuidString, isDirectory: true)
    defer { try? FileManager.default.removeItem(at: directory) }
    let outbox = EncryptedOutbox(
        directory: directory,
        key: SymmetricKey(data: Data(repeating: 0x32, count: 32))
    )
    let command = StartVoiceSession(identity: outboxIdentity, permission: GrantedPermission(), sink: outbox)
    let first = try await command.execute(values: InvocationValues(
        eventID: UUID(), turnID: UUID(), occurredAt: Date(timeIntervalSince1970: 1), sequence: 1
    ))
    let second = try await command.execute(values: InvocationValues(
        eventID: UUID(), turnID: UUID(), occurredAt: Date(timeIntervalSince1970: 2), sequence: 2
    ))

    // When: transport acknowledgment removes the first event.
    try await outbox.acknowledge(eventIDs: [first.eventId])

    // Then: the later event remains queued.
    #expect(try await outbox.pendingEvents() == [second])
}

@Test("conversation purge removes only matching events and resets its delivery cursor")
func purgingConversationKeepsOtherQueues() async throws {
    let directory = FileManager.default.temporaryDirectory
        .appendingPathComponent(UUID().uuidString, isDirectory: true)
    defer { try? FileManager.default.removeItem(at: directory) }
    let outbox = EncryptedOutbox(
        directory: directory,
        key: SymmetricKey(data: Data(repeating: 0x33, count: 32))
    )
    let command = StartVoiceSession(identity: outboxIdentity, permission: GrantedPermission(), sink: outbox)
    let first = try await command.execute(values: InvocationValues(
        eventID: UUID(), turnID: UUID(), occurredAt: Date(), sequence: 1
    ))
    let otherIdentity = ConversationIdentity(
        conversationID: UUID(), participantID: outboxIdentity.participantID,
        deviceID: outboxIdentity.deviceID
    )
    let other = try await StartVoiceSession(
        identity: otherIdentity, permission: GrantedPermission(), sink: outbox
    ).execute(values: InvocationValues(
        eventID: UUID(), turnID: UUID(), occurredAt: Date(), sequence: 1
    ))
    try await outbox.acknowledge(eventIDs: [first.eventId])
    try await outbox.purge(conversationID: outboxIdentity.conversationID)
    #expect(try await outbox.pendingEvents() == [other])
    #expect(try await outbox.nextSequence(conversationID: outboxIdentity.conversationID) == 1)
}

@Test("delivered history cache is encrypted, restartable, and purgeable")
func encryptedHistoryCacheLifecycle() async throws {
    let directory = FileManager.default.temporaryDirectory
        .appendingPathComponent(UUID().uuidString, isDirectory: true)
    defer { try? FileManager.default.removeItem(at: directory) }
    let key = SymmetricKey(data: Data(repeating: 0x34, count: 32))
    let outbox = EncryptedOutbox(directory: directory, key: key)
    let event = try await StartVoiceSession(
        identity: outboxIdentity, permission: GrantedPermission(), sink: outbox
    ).execute(values: InvocationValues(
        eventID: UUID(), turnID: UUID(), occurredAt: Date(), sequence: 1
    ))
    let cache = EncryptedConversationCache(directory: directory, key: key)
    try await cache.replace(conversationID: outboxIdentity.conversationID, events: [event])
    #expect(try await cache.events(conversationID: outboxIdentity.conversationID) == [event])
    #expect(!String(decoding: try Data(contentsOf: cache.fileURL), as: UTF8.self).contains("voice_session_started"))
    let reopened = EncryptedConversationCache(directory: directory, key: key)
    #expect(try await reopened.events(conversationID: outboxIdentity.conversationID) == [event])
    try await reopened.purge(conversationID: outboxIdentity.conversationID)
    #expect(try await reopened.events(conversationID: outboxIdentity.conversationID).isEmpty)
    #expect(!FileManager.default.fileExists(atPath: reopened.fileURL.path))
}
