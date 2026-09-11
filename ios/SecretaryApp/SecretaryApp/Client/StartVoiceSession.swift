import Foundation
import SecretaryContract

/// Error thrown by voice-session commands when the microphone cannot be used.
public enum StartVoiceSessionError: Error, Sendable {
    case permissionDenied
}

/// The shared voice-session command. Every invocation surface routes through
/// this command via `InvocationAdapter`.
public struct StartVoiceSession: ConversationCommand {
    public let identity: ConversationIdentity
    public let permission: any MicrophonePermissionProviding
    public let sink: any ConversationEventSink

    public init(
        identity: ConversationIdentity,
        permission: any MicrophonePermissionProviding,
        sink: any ConversationEventSink
    ) {
        self.identity = identity
        self.permission = permission
        self.sink = sink
    }

    public func execute(values: InvocationValues) async throws -> ConversationEvent {
        let authorization = await permission.requestAuthorization()
        guard authorization == .granted else {
            throw StartVoiceSessionError.permissionDenied
        }

        let event = try ConversationEventFactory.voiceSessionStarted(
            identity: identity,
            values: values
        )
        try await sink.send(event)
        return event
    }
}
