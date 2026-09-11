import SecretaryContract

/// Destination for conversation events produced by client commands. The
/// in-memory transcript, the encrypted offline outbox, and the future network
/// transport all conform to this protocol.
public protocol ConversationEventSink: Sendable {
    func send(_ event: ConversationEvent) async throws
}

public protocol ConversationEventBatchSink: ConversationEventSink {
    func send(_ events: [ConversationEvent]) async throws
}
