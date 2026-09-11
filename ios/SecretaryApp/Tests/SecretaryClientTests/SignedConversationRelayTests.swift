import CryptoKit
import Foundation
import SecretaryClient
import SecretaryContract
import Testing

private let relayIdentity = ConversationIdentity(
    conversationID: UUID(uuidString: "55555555-5555-4555-8555-555555555555")!,
    participantID: UUID(uuidString: "11111111-1111-4111-8111-111111111111")!,
    deviceID: UUID(uuidString: "33333333-3333-4333-8333-333333333333")!
)

private actor RecordingRelayTransport: RelayHTTPTransport {
    private let responses: [RelayHTTPResponse]
    private var requests: [URLRequest] = []

    init(_ responses: [RelayHTTPResponse]) { self.responses = responses }

    func send(_ request: URLRequest) async throws -> RelayHTTPResponse {
        requests.append(request)
        return responses[min(requests.count - 1, responses.count - 1)]
    }

    func recorded() -> [URLRequest] { requests }
}

private actor AutoAcknowledgingRelay: RelayHTTPTransport {
    private var sizes: [Int] = []
    func send(_ request: URLRequest) async throws -> RelayHTTPResponse {
        struct Batch: Decodable { let events: [ConversationEvent] }
        let body = request.httpBody!
        sizes.append(body.count)
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .lifeOSISO8601
        let batch = try decoder.decode(Batch.self, from: body)
        return RelayHTTPResponse(status: 200, body: try JSONSerialization.data(withJSONObject: [
            "acknowledged_event_ids": batch.events.map { $0.eventId.uuidString },
            "next_sequence": batch.events.last!.sequence + 1,
        ]))
    }
    func bodySizes() -> [Int] { sizes }
}

private func queuedText(_ outbox: EncryptedOutbox) async throws -> [ConversationEvent] {
    try await SendTextMessage(identity: relayIdentity, sink: outbox).execute(TextMessageRequest(
        text: "Private queued message",
        identifiers: TextMessageIdentifiers(partialEventID: UUID(), finalEventID: UUID(), turnID: UUID()),
        position: TextMessagePosition(occurredAt: Date(timeIntervalSince1970: 1767225600), startingSequence: 1)
    ))
}

private func acknowledgment(_ events: [ConversationEvent]) throws -> Data {
    try JSONSerialization.data(withJSONObject: [
        "acknowledged_event_ids": events.map { $0.eventId.uuidString }, "next_sequence": 3,
    ])
}

@Test("Swift request signing matches the Python Ed25519 golden vector")
func relaySigningMatchesPython() throws {
    // Public deterministic TEST key, never a real enrollment key.
    let key = try Curve25519.Signing.PrivateKey(rawRepresentation: Data(repeating: 0x42, count: 32))
    let proof = RelayRequestProof(
        deviceID: relayIdentity.deviceID,
        requestID: UUID(uuidString: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")!, issuedAt: 1767225600
    )
    let bytes = try proof.signingBytes(method: "POST", target: "/v1/conversations", body: Data("{}".utf8))
    #expect(String(data: bytes, encoding: .ascii) == "lifeos.request.v1\nPOST\n/v1/conversations\n33333333-3333-4333-8333-333333333333\naaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa\n1767225600\n44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a")
    #expect(key.publicKey.rawRepresentation.map { String(format: "%02x", $0) }.joined() == "2152f8d19b791d24453242e15f2eab6cb7cffa7b6a5ed30097960e069881db12")
    // CryptoKit may randomize the nonce while producing valid Ed25519 signatures.
    // Verify the Python vector instead of assuming identical signature bytes.
    let encoded = "0ba67184cc9ec94457127154b682efe61604371fdf0155b5bf34dc62d26c34ab99c2bb8fb600d52d2c924f30358aa42e146c224ba14bea0dc1c8f143b6ced50f"
    let signature = Data(stride(from: 0, to: encoded.count, by: 2).map { index in
        UInt8(encoded.dropFirst(index).prefix(2), radix: 16)!
    })
    #expect(key.publicKey.isValidSignature(signature, for: bytes))
}

@Test("delivery retries preserve event bytes, refresh request nonces, and retain the cursor after restart")
func relayRetryAndAcknowledgment() async throws {
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: directory) }
    let key = SymmetricKey(size: .bits256)
    let outbox = EncryptedOutbox(directory: directory, key: key)
    let events = try await queuedText(outbox)
    let transport = RecordingRelayTransport([
        RelayHTTPResponse(status: 503, body: Data()),
        RelayHTTPResponse(status: 200, body: try acknowledgment(events)),
    ])
    let signingKey = Curve25519.Signing.PrivateKey()
    let client = try SignedConversationRelay(
        origin: URL(string: "https://localhost:8443")!, deviceID: relayIdentity.deviceID,
        signingKey: signingKey, transport: transport
    )
    await #expect(throws: ConversationRelayError.httpStatus(503)) { try await client.flush(outbox) }
    #expect(try await outbox.pendingEvents() == events)
    let ack = try await client.flush(outbox)
    #expect(ack?.acknowledgedEventIDs == events.map(\.eventId))
    #expect(try await outbox.pendingEvents().isEmpty)
    let reopened = EncryptedOutbox(directory: directory, key: key)
    #expect(try await reopened.nextSequence(conversationID: relayIdentity.conversationID) == 3)
    let requests = await transport.recorded()
    #expect(requests[0].httpBody == requests[1].httpBody)
    #expect(requests[0].value(forHTTPHeaderField: "X-LifeOS-Request-ID") != requests[1].value(forHTTPHeaderField: "X-LifeOS-Request-ID"))
    for request in requests {
        let proof = RelayRequestProof(
            deviceID: UUID(uuidString: request.value(forHTTPHeaderField: "X-LifeOS-Device")!)!,
            requestID: UUID(uuidString: request.value(forHTTPHeaderField: "X-LifeOS-Request-ID")!)!,
            issuedAt: Int64(request.value(forHTTPHeaderField: "X-LifeOS-Issued-At")!)!
        )
        let encoded = request.value(forHTTPHeaderField: "X-LifeOS-Signature")!
        let signature = Data(stride(from: 0, to: encoded.count, by: 2).map { index in
            UInt8(encoded.dropFirst(index).prefix(2), radix: 16)!
        })
        let bytes = try proof.signingBytes(method: "POST", target: request.url!.path, body: request.httpBody!)
        #expect(signingKey.publicKey.isValidSignature(signature, for: bytes))
    }
}

@Test("foreign or incomplete acknowledgments never remove queued messages")
func relayRejectsInvalidAcknowledgment() async throws {
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: directory) }
    let outbox = EncryptedOutbox(directory: directory, key: SymmetricKey(size: .bits256))
    let events = try await queuedText(outbox)
    let transport = RecordingRelayTransport([RelayHTTPResponse(status: 200, body: try acknowledgment([]))])
    let client = try SignedConversationRelay(
        origin: URL(string: "https://localhost")!, deviceID: relayIdentity.deviceID,
        signingKey: Curve25519.Signing.PrivateKey(), transport: transport
    )
    await #expect(throws: ConversationRelayError.invalidResponse) { try await client.flush(outbox) }
    #expect(try await outbox.pendingEvents() == events)
}

@Test("plaintext, credential-bearing and path-prefixed relay endpoints are rejected")
func relayRejectsUnsafeEndpoints() {
    let transport = RecordingRelayTransport([])
    for value in ["http://localhost", "https://user:password@localhost", "https://localhost/api", "https://localhost?token=value", "https://localhost#fragment"] {
        #expect(throws: ConversationRelayError.invalidEndpoint) {
            _ = try SignedConversationRelay(
                origin: URL(string: value)!, deviceID: relayIdentity.deviceID,
                signingKey: Curve25519.Signing.PrivateKey(), transport: transport
            )
        }
    }
}

@Test("redirect responses preserve the encrypted outbox")
func relayRejectsRedirectResponse() async throws {
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: directory) }
    let outbox = EncryptedOutbox(directory: directory, key: SymmetricKey(size: .bits256))
    let events = try await queuedText(outbox)
    let transport = RecordingRelayTransport([RelayHTTPResponse(status: 302, body: Data())])
    let client = try SignedConversationRelay(
        origin: URL(string: "https://localhost")!, deviceID: relayIdentity.deviceID,
        signingKey: Curve25519.Signing.PrivateKey(), transport: transport
    )
    await #expect(throws: ConversationRelayError.httpStatus(302)) { try await client.flush(outbox) }
    #expect(try await outbox.pendingEvents() == events)
}

@Test("large offline queues are divided into byte-bounded batches without losing events")
func relaySplitsLargeQueuedBatches() async throws {
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: directory) }
    let outbox = EncryptedOutbox(directory: directory, key: SymmetricKey(size: .bits256))
    let command = SendTextMessage(identity: relayIdentity, sink: outbox)
    for index in 0..<20 {
        _ = try await command.execute(TextMessageRequest(
            text: String(repeating: "x", count: 10000),
            identifiers: TextMessageIdentifiers(partialEventID: UUID(), finalEventID: UUID(), turnID: UUID()),
            position: TextMessagePosition(occurredAt: Date(), startingSequence: index * 2 + 1)
        ))
    }
    let transport = AutoAcknowledgingRelay()
    let client = try SignedConversationRelay(
        origin: URL(string: "https://localhost")!, deviceID: relayIdentity.deviceID,
        signingKey: Curve25519.Signing.PrivateKey(), transport: transport
    )
    var delivered = 0
    while let acknowledgment = try await client.flush(outbox) { delivered += acknowledgment.acknowledgedEventIDs.count }
    #expect(delivered == 40)
    #expect(try await outbox.pendingEvents().isEmpty)
    #expect(try await outbox.nextSequence(conversationID: relayIdentity.conversationID) == 41)
    let sizes = await transport.bodySizes()
    #expect(sizes.count > 1)
    #expect(sizes.allSatisfy { $0 <= 256 * 1024 })
}

@Test("oversized text is rejected before it enters the encrypted queue")
func oversizedRelayTextIsNotQueued() async throws {
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: directory) }
    let outbox = EncryptedOutbox(directory: directory, key: SymmetricKey(size: .bits256))
    let request = try TextMessageRequest(
        text: String(repeating: "x", count: 16000),
        identifiers: TextMessageIdentifiers(partialEventID: UUID(), finalEventID: UUID(), turnID: UUID()),
        position: TextMessagePosition(occurredAt: Date(), startingSequence: 1)
    )
    await #expect(throws: TextMessageError.messageTooLarge) {
        try await SendTextMessage(identity: relayIdentity, sink: outbox).execute(request)
    }
    #expect(try await outbox.pendingEvents().isEmpty)
}

@Test("conversation deletion is a signed empty-body request")
func relayDeletionRequestIsSigned() async throws {
    let response = try JSONSerialization.data(withJSONObject: ["deleted": true])
    let transport = RecordingRelayTransport([RelayHTTPResponse(status: 200, body: response)])
    let signingKey = Curve25519.Signing.PrivateKey()
    let client = try SignedConversationRelay(
        origin: URL(string: "https://localhost")!, deviceID: relayIdentity.deviceID,
        signingKey: signingKey, transport: transport
    )
    try await client.deleteConversation(relayIdentity.conversationID)
    let request = try #require(await transport.recorded().first)
    #expect(request.httpMethod == "DELETE")
    #expect(request.httpBody?.isEmpty != false)
    let proof = RelayRequestProof(
        deviceID: UUID(uuidString: request.value(forHTTPHeaderField: "X-LifeOS-Device")!)!,
        requestID: UUID(uuidString: request.value(forHTTPHeaderField: "X-LifeOS-Request-ID")!)!,
        issuedAt: Int64(request.value(forHTTPHeaderField: "X-LifeOS-Issued-At")!)!
    )
    let encoded = request.value(forHTTPHeaderField: "X-LifeOS-Signature")!
    let signature = Data(stride(from: 0, to: encoded.count, by: 2).map { index in
        UInt8(encoded.dropFirst(index).prefix(2), radix: 16)!
    })
    #expect(signingKey.publicKey.isValidSignature(
        signature,
        for: try proof.signingBytes(method: "DELETE", target: request.url!.path, body: Data())
    ))
}
