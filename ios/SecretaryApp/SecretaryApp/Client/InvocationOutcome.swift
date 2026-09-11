import SecretaryContract

/// The result of routing an invocation through the adapter.
public enum InvocationOutcome: Sendable, Equatable {
    /// A conversation event was produced and delivered to the sink.
    case started(ConversationEvent)
    /// Microphone permission was denied; the caller should fall back to text.
    case permissionDenied(fallback: InvocationFallback)
    /// The invocation could not be mapped to a valid voice session start.
    case invalidInvocation
}

/// Degraded-mode alternatives offered when a voice surface cannot start.
public enum InvocationFallback: Sendable, Equatable {
    case textConversation
}