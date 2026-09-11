import Foundation

/// Identifies the conversation, participant, and device that a client-side
/// voice session belongs to. Values are supplied by the host app; the client
/// package never fabricates them.
public struct ConversationIdentity: Sendable, Equatable {
    public let conversationID: UUID
    public let participantID: UUID
    public let deviceID: UUID

    public init(conversationID: UUID, participantID: UUID, deviceID: UUID) {
        self.conversationID = conversationID
        self.participantID = participantID
        self.deviceID = deviceID
    }
}