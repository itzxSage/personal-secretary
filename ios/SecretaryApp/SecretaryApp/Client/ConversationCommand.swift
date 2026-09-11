import SecretaryContract

/// A single unit of conversation work. Every invocation surface funnels into
/// a command so new public invocation sources do not change Voice Session or
/// Conversation API semantics.
public protocol ConversationCommand: Sendable {
    func execute(values: InvocationValues) async throws -> ConversationEvent
}
