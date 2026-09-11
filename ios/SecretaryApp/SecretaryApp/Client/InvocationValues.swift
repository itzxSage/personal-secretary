import Foundation

/// Per-invocation identifiers and timestamp. The host app generates these so
/// every surface produces a distinct, replayable conversation event.
public struct InvocationValues: Sendable, Equatable {
    public let eventID: UUID
    public let turnID: UUID
    public let occurredAt: Date
    public let sequence: Int

    public init(eventID: UUID, turnID: UUID, occurredAt: Date, sequence: Int) {
        self.eventID = eventID
        self.turnID = turnID
        self.occurredAt = occurredAt
        self.sequence = sequence
    }
}
