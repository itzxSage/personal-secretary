import Foundation
import SecretaryContract

enum ConversationEventFactory {
    static func voiceSessionStarted(
        identity: ConversationIdentity,
        values: InvocationValues
    ) throws -> ConversationEvent {
        try makeTranscriptEvent(
            identity: identity,
            values: values,
            payload: WireTranscript(
                turnId: values.turnID,
                text: "voice_session_started",
                isFinal: false
            )
        )
    }

    static func voiceSessionEnded(
        identity: ConversationIdentity,
        values: InvocationValues
    ) throws -> ConversationEvent {
        let payload = WireCancellation(
            turnId: values.turnID,
            reason: "user_ended_voice_session"
        )
        return try decode(
            WireConversationEvent(
                eventId: values.eventID,
                conversationId: identity.conversationID,
                participantId: identity.participantID,
                deviceId: identity.deviceID,
                sequence: values.sequence,
                occurredAt: values.occurredAt,
                kind: .cancellation,
                payload: payload
            )
        )
    }

    static func textMessage(
        identity: ConversationIdentity,
        request: TextMessageRequest
    ) throws -> [ConversationEvent] {
        let partial = try makeTranscriptEvent(
            identity: identity,
            values: InvocationValues(
                eventID: request.identifiers.partialEventID,
                turnID: request.identifiers.turnID,
                occurredAt: request.position.occurredAt,
                sequence: request.position.startingSequence
            ),
            payload: WireTranscript(
                turnId: request.identifiers.turnID,
                text: request.text,
                isFinal: false
            )
        )
        let final = try makeTranscriptEvent(
            identity: identity,
            values: InvocationValues(
                eventID: request.identifiers.finalEventID,
                turnID: request.identifiers.turnID,
                occurredAt: request.position.occurredAt,
                sequence: request.position.startingSequence + 1
            ),
            payload: WireTranscript(
                turnId: request.identifiers.turnID,
                text: request.text,
                isFinal: true
            )
        )
        return [partial, final]
    }

    private static func makeTranscriptEvent(
        identity: ConversationIdentity,
        values: InvocationValues,
        payload: WireTranscript
    ) throws -> ConversationEvent {
        let wireEvent = WireConversationEvent(
            eventId: values.eventID,
            conversationId: identity.conversationID,
            participantId: identity.participantID,
            deviceId: identity.deviceID,
            sequence: values.sequence,
            occurredAt: values.occurredAt,
            kind: payload.eventKind,
            payload: payload
        )
        return try decode(wireEvent)
    }

    private static func decode<Payload: Encodable>(
        _ wireEvent: WireConversationEvent<Payload>
    ) throws -> ConversationEvent {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        let data = try encoder.encode(wireEvent)
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .lifeOSISO8601
        return try decoder.decode(ConversationEvent.self, from: data)
    }
}

private struct WireConversationEvent<Payload: Encodable>: Encodable {
    let eventId: UUID
    let conversationId: UUID
    let participantId: UUID
    let deviceId: UUID
    let sequence: Int
    let occurredAt: Date
    let kind: ConversationEventKind
    let payload: Payload

    private enum CodingKeys: String, CodingKey {
        case eventId = "event_id"
        case conversationId = "conversation_id"
        case participantId = "participant_id"
        case deviceId = "device_id"
        case sequence
        case occurredAt = "occurred_at"
        case kind
        case payload
    }
}

private struct WireCancellation: Encodable {
    let turnId: UUID
    let reason: String

    private enum CodingKeys: String, CodingKey {
        case kind
        case turnId = "turn_id"
        case reason
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(ConversationEventKind.cancellation.rawValue, forKey: .kind)
        try container.encode(turnId, forKey: .turnId)
        try container.encode(reason, forKey: .reason)
    }
}

private struct WireTranscript: Encodable {
    let turnId: UUID
    let text: String
    let isFinal: Bool

    var eventKind: ConversationEventKind {
        isFinal ? .transcriptFinal : .transcriptPartial
    }

    private enum CodingKeys: String, CodingKey {
        case kind
        case turnId = "turn_id"
        case text
        case isFinal = "is_final"
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(eventKind.rawValue, forKey: .kind)
        try container.encode(turnId, forKey: .turnId)
        try container.encode(text, forKey: .text)
        try container.encode(isFinal, forKey: .isFinal)
    }
}
