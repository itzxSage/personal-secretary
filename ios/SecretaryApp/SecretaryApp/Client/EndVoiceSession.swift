import SecretaryContract

/// Closes a locally active voice turn with a durable cancellation event.
public struct EndVoiceSession: ConversationCommand {
    public let identity: ConversationIdentity
    public let sink: any ConversationEventSink

    public init(identity: ConversationIdentity, sink: any ConversationEventSink) {
        self.identity = identity
        self.sink = sink
    }

    public func execute(values: InvocationValues) async throws -> ConversationEvent {
        let event = try ConversationEventFactory.voiceSessionEnded(
            identity: identity,
            values: values
        )
        try await sink.send(event)
        return event
    }
}
