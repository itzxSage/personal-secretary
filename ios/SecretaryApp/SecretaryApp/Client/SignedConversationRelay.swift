import CryptoKit
import Foundation
import SecretaryContract

public enum ConversationRelayError: Error, Equatable, Sendable {
    case invalidEndpoint
    case invalidBatch
    case invalidResponse
    case responseTooLarge
    case httpStatus(Int)
    case deliveryInProgress
}

public struct RelayHTTPResponse: Sendable {
    public let status: Int
    public let body: Data

    public init(status: Int, body: Data) {
        self.status = status
        self.body = body
    }
}

public protocol RelayHTTPTransport: Sendable {
    func send(_ request: URLRequest) async throws -> RelayHTTPResponse
}

/// Domain separation prevents a transport signature from authorizing a proposal.
public struct RelayRequestProof: Sendable {
    public let deviceID: UUID
    public let requestID: UUID
    public let issuedAt: Int64

    public init(deviceID: UUID, requestID: UUID, issuedAt: Int64) {
        self.deviceID = deviceID
        self.requestID = requestID
        self.issuedAt = issuedAt
    }

    public func signingBytes(method: String, target: String, body: Data) throws -> Data {
        let fields = [
            "lifeos.request.v1", method, target,
            deviceID.uuidString.lowercased(), requestID.uuidString.lowercased(),
            String(issuedAt), SHA256.hash(data: body).map { String(format: "%02x", $0) }.joined(),
        ]
        guard fields.allSatisfy({ !$0.contains("\n") && !$0.contains("\r") }),
            let data = fields.joined(separator: "\n").data(using: .ascii) else {
            throw ConversationRelayError.invalidEndpoint
        }
        return data
    }
}

public struct RelayEventAcknowledgment: Codable, Sendable {
    public let acknowledgedEventIDs: [UUID]
    public let nextSequence: Int

    enum CodingKeys: String, CodingKey {
        case acknowledgedEventIDs = "acknowledged_event_ids"
        case nextSequence = "next_sequence"
    }
}

public struct RelayEventPage: Codable, Sendable {
    public let events: [ConversationEvent]
    public let cursor: Int
}

private struct RelayDeletion: Decodable { let deleted: Bool }

private struct RelayEventBatch: Encodable { let events: [ConversationEvent] }

/// Signed HTTPS transport, independent of provider credentials or execution authority.
/// A retry uses a fresh request nonce but preserves every queued event ID and byte of content.
public actor SignedConversationRelay {
    private let origin: URL
    private let deviceID: UUID
    private let signingKey: Curve25519.Signing.PrivateKey
    private let transport: any RelayHTTPTransport
    private var isDelivering = false

    public init(
        origin: URL, deviceID: UUID, signingKey: Curve25519.Signing.PrivateKey,
        transport: any RelayHTTPTransport
    ) throws {
        guard origin.scheme == "https", origin.host?.isEmpty == false,
            origin.user == nil, origin.password == nil, origin.query == nil,
            origin.fragment == nil, origin.path.isEmpty || origin.path == "/" else {
            throw ConversationRelayError.invalidEndpoint
        }
        self.origin = origin
        self.deviceID = deviceID
        self.signingKey = signingKey
        self.transport = transport
    }

    public func create(conversationID: UUID, title: String) async throws -> Conversation {
        struct Creation: Encodable {
            let conversation_id: UUID
            let title: String
        }
        guard !title.isEmpty, title.count <= 200 else { throw ConversationRelayError.invalidBatch }
        let body = try Self.encoder().encode(Creation(conversation_id: conversationID, title: title))
        let conversation: Conversation = try await request("POST", "/v1/conversations", body: body)
        guard conversation.conversationId == conversationID,
            conversation.deviceIds.contains(deviceID) else {
            throw ConversationRelayError.invalidResponse
        }
        return conversation
    }

    public func deliver(_ events: [ConversationEvent]) async throws -> RelayEventAcknowledgment {
        guard let first = events.first, events.count <= 100,
            Set(events.map(\.eventId)).count == events.count,
            events.allSatisfy({ $0.deviceId == deviceID && $0.conversationId == first.conversationId }) else {
            throw ConversationRelayError.invalidBatch
        }
        let body = try Self.encoder().encode(RelayEventBatch(events: events))
        guard body.count <= 256 * 1024 else { throw ConversationRelayError.invalidBatch }
        let target = "/v1/conversations/\(first.conversationId.uuidString.lowercased())/events"
        let acknowledgment: RelayEventAcknowledgment = try await request("POST", target, body: body)
        guard acknowledgment.acknowledgedEventIDs == events.map(\.eventId),
            acknowledgment.nextSequence > (events.map(\.sequence).max() ?? 0) else {
            throw ConversationRelayError.invalidResponse
        }
        return acknowledgment
    }

    public func resume(conversationID: UUID, after: Int = 0) async throws -> RelayEventPage {
        guard after >= 0 else { throw ConversationRelayError.invalidBatch }
        let target = "/v1/conversations/\(conversationID.uuidString.lowercased())/events?after=\(after)&limit=100"
        let page: RelayEventPage = try await request("GET", target)
        guard page.events.count <= 100, page.cursor == (page.events.last?.sequence ?? after),
            page.events.enumerated().allSatisfy({ offset, event in
                event.conversationId == conversationID && event.sequence == after + offset + 1
            }) else {
            throw ConversationRelayError.invalidResponse
        }
        return page
    }

    public func deleteConversation(_ conversationID: UUID) async throws {
        let target = "/v1/conversations/\(conversationID.uuidString.lowercased())"
        let result: RelayDeletion = try await request("DELETE", target)
        guard result.deleted else { throw ConversationRelayError.invalidResponse }
    }

    /// Snapshot one bounded batch. Failed delivery or a malformed ACK leaves the queue untouched.
    public func flush(_ outbox: EncryptedOutbox) async throws -> RelayEventAcknowledgment? {
        guard !isDelivering else { throw ConversationRelayError.deliveryInProgress }
        isDelivering = true
        defer { isDelivering = false }
        let pending = try await outbox.pendingEvents()
        guard let first = pending.first else { return nil }
        var batch: [ConversationEvent] = []
        for event in pending.prefix(while: { $0.conversationId == first.conversationId }).prefix(100) {
            let candidate = batch + [event]
            if try Self.encoder().encode(RelayEventBatch(events: candidate)).count > 256 * 1024 { break }
            batch = candidate
        }
        guard !batch.isEmpty else { throw ConversationRelayError.invalidBatch }
        let acknowledgment = try await deliver(batch)
        try await outbox.acknowledge(eventIDs: acknowledgment.acknowledgedEventIDs)
        return acknowledgment
    }

    private func request<Response: Decodable>(
        _ method: String, _ target: String, body: Data = Data()
    ) async throws -> Response {
        guard let url = URL(string: target, relativeTo: origin)?.absoluteURL,
            url.host == origin.host, url.scheme == "https", url.port == origin.port else {
            throw ConversationRelayError.invalidEndpoint
        }
        let proof = RelayRequestProof(
            deviceID: deviceID, requestID: UUID(), issuedAt: Int64(Date().timeIntervalSince1970)
        )
        let signature = try signingKey.signature(for: proof.signingBytes(method: method, target: target, body: body))
        var request = URLRequest(url: url)
        request.httpMethod = method
        if method != "GET" { request.httpBody = body }
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("1.0.0", forHTTPHeaderField: "X-LifeOS-Contract-Version")
        request.setValue(proof.deviceID.uuidString.lowercased(), forHTTPHeaderField: "X-LifeOS-Device")
        request.setValue(proof.requestID.uuidString.lowercased(), forHTTPHeaderField: "X-LifeOS-Request-ID")
        request.setValue(String(proof.issuedAt), forHTTPHeaderField: "X-LifeOS-Issued-At")
        request.setValue(signature.map { String(format: "%02x", $0) }.joined(), forHTTPHeaderField: "X-LifeOS-Signature")
        let response = try await transport.send(request)
        guard response.status == 200 else { throw ConversationRelayError.httpStatus(response.status) }
        guard response.body.count <= 2 * 1024 * 1024 else { throw ConversationRelayError.responseTooLarge }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .lifeOSISO8601
        return try decoder.decode(Response.self, from: response.body)
    }

    private static func encoder() -> JSONEncoder {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        return encoder
    }
}
