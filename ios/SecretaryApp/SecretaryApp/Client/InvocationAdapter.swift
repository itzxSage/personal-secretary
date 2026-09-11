import Foundation

/// Routes every invocation source through the shared voice-session command.
public struct InvocationAdapter: Sendable {
    public let startVoiceSession: any ConversationCommand

    public init(startVoiceSession: any ConversationCommand) {
        self.startVoiceSession = startVoiceSession
    }

    public func invoke(_ source: InvocationSource, values: InvocationValues) async -> InvocationOutcome {
        do {
            let event = try await startVoiceSession.execute(values: values)
            return .started(event)
        } catch StartVoiceSessionError.permissionDenied {
            return .permissionDenied(fallback: .textConversation)
        } catch {
            return .invalidInvocation
        }
    }
}
