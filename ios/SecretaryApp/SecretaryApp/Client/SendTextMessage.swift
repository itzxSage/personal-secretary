import Foundation
import SecretaryContract

public enum TextMessageError: Error, Sendable, Equatable {
    case emptyMessage
    case messageTooLarge
}

public struct TextMessageIdentifiers: Sendable, Equatable {
    public let partialEventID: UUID
    public let finalEventID: UUID
    public let turnID: UUID

    public init(partialEventID: UUID, finalEventID: UUID, turnID: UUID) {
        self.partialEventID = partialEventID
        self.finalEventID = finalEventID
        self.turnID = turnID
    }
}

public struct TextMessagePosition: Sendable, Equatable {
    public let occurredAt: Date
    public let startingSequence: Int

    public init(occurredAt: Date, startingSequence: Int) {
        self.occurredAt = occurredAt
        self.startingSequence = startingSequence
    }
}

public struct TextMessageRequest: Sendable, Equatable {
    public let text: String
    public let identifiers: TextMessageIdentifiers
    public let position: TextMessagePosition

    public init(
        text: String,
        identifiers: TextMessageIdentifiers,
        position: TextMessagePosition
    ) throws {
        let parsedText = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !parsedText.isEmpty else {
            throw TextMessageError.emptyMessage
        }
        self.text = parsedText
        self.identifiers = identifiers
        self.position = position
    }
}

public struct SendTextMessage: Sendable {
    public let identity: ConversationIdentity
    public let sink: any ConversationEventSink

    public init(identity: ConversationIdentity, sink: any ConversationEventSink) {
        self.identity = identity
        self.sink = sink
    }

    public func execute(_ request: TextMessageRequest) async throws -> [ConversationEvent] {
        let events = try ConversationEventFactory.textMessage(identity: identity, request: request)
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        for event in events {
            // Leave room for server-side canonical normalization within its 16 KiB limit.
            guard try encoder.encode(event).count <= 15 * 1024 else {
                throw TextMessageError.messageTooLarge
            }
        }
        if let batchSink = sink as? any ConversationEventBatchSink {
            try await batchSink.send(events)
            return events
        }
        for event in events {
            try await sink.send(event)
        }
        return events
    }
}
