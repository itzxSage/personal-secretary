import CryptoKit
import Foundation
import SecretaryClient
import Testing

private let weekPlanDeviceID = UUID(uuidString: "33333333-3333-4333-8333-333333333333")!
private let weekPlanProposalID = UUID(uuidString: "22222222-2222-4222-8222-222222222222")!

private actor WeekPlanningTransport: RelayHTTPTransport {
    private let response: RelayHTTPResponse
    private var requests: [URLRequest] = []

    init(status: Int = 200, body: Data) {
        response = RelayHTTPResponse(status: status, body: body)
    }

    func send(_ request: URLRequest) async throws -> RelayHTTPResponse {
        requests.append(request)
        return response
    }

    func recordedRequest() -> URLRequest? { requests.first }
    func recordedRequests() -> [URLRequest] { requests }
}

private func weekPlanFixture(status: String = "partial") -> Data {
    Data("""
    {
      "proposal_id": "22222222-2222-4222-8222-222222222222",
      "payload_hash": "abcd",
      "status": "\(status)",
      "plan_date": "2026-09-13",
      "timezone": "America/Chicago",
      "window_start": "2026-09-13T05:00:00-05:00",
      "window_end": "2026-09-20T05:00:00-05:00",
      "blocks": [{"block_id":"fixed-1","activity_id":null,"title":"Sleep","kind":"protected","starts_at":"2026-09-13T23:00:00-05:00","ends_at":"2026-09-14T07:00:00-05:00","calendar_eligible":false}],
      "calendar_blocks": [{"block_id":"work-1","display_group":"Deep work","title":"Draft brief","starts_at":"2026-09-14T09:00:00-05:00","ends_at":"2026-09-14T10:00:00-05:00","source_activity_ids":["activity-1"],"segment_titles":["Draft"],"transitions":["Commute"],"explanation_codes":["scheduled"]}],
      "explanations": [{"activity_id":"activity-1","code":"scheduled","summary":"Best energy window","score":{"deadline":1,"dependency":2,"travel":3,"energy":4,"goal":5,"importance":6,"total":21},"scheduled_block_ids":["work-1"],"constraints":["before lunch"]}],
      "unscheduled": [{"activity_id":"activity-2","reason":"capacity","detail":"No eligible opening","blocking_activity_ids":["activity-1"]}],
      "gaps": [{"memory_id":"44444444-4444-4444-8444-444444444444","title":"Dentist","reason":"unknown_time","explanation":"Choose a time"}],
      "calendar_dry_run": {"calendar_summary":"One insertion","operations":[{"operation":"insert","event_id":"event-1","before_summary":null,"after_summary":"Draft brief"}]}
    }
    """.utf8)
}

private func makeWeekPlanningRelay(
    transport: WeekPlanningTransport,
    signingKey: Curve25519.Signing.PrivateKey = Curve25519.Signing.PrivateKey()
) throws -> SignedConversationRelay {
    try SignedConversationRelay(
        origin: URL(string: "https://localhost:8443")!, deviceID: weekPlanDeviceID,
        signingKey: signingKey, transport: transport
    )
}

private func approvalInput() -> LifeOSApprovalInput {
    LifeOSApprovalInput(
        factID: UUID(uuidString: "f1111111-1111-4111-8111-111111111111")!,
        proposalID: weekPlanProposalID,
        payloadHash: "abcd",
        issuedAt: Date(timeIntervalSince1970: 1_789_325_762),
        expiresAt: Date(timeIntervalSince1970: 1_789_326_062),
        idempotencyKey: "week-plan-approve",
        actor: weekPlanDeviceID.uuidString.lowercased(),
        correlationID: UUID(uuidString: "c1c1c1c1-c1c1-c1c1-c1c1-c1c1c1c1c1c1")!,
        deviceID: weekPlanDeviceID
    )
}

@Test("week-plan preview is a signed POST with an empty body and decodes the bounded proposal")
func previewWeekPlanContract() async throws {
    let signingKey = Curve25519.Signing.PrivateKey()
    let transport = WeekPlanningTransport(body: weekPlanFixture())
    let relay = try makeWeekPlanningRelay(transport: transport, signingKey: signingKey)

    let proposal = try await relay.previewWeekPlan()

    #expect(proposal.proposalID == weekPlanProposalID)
    #expect(proposal.status == .partial)
    #expect(proposal.blocks.first?.kind == .protected)
    #expect(proposal.calendarBlocks.first?.sourceActivityIDs == ["activity-1"])
    #expect(proposal.explanations.first?.score.total == 21)
    #expect(proposal.unscheduled.first?.reason == "capacity")
    #expect(proposal.gaps.first?.reason == .unknownTime)
    #expect(proposal.calendarDryRun.operations.first?.operation == .insert)
    let request = try #require(await transport.recordedRequest())
    #expect(request.url?.path == "/v1/week-plan")
    #expect(request.httpMethod == "POST")
    #expect(request.httpBody == Data())
    #expect(request.value(forHTTPHeaderField: "Content-Type") == "application/json")
    #expect(request.value(forHTTPHeaderField: "X-LifeOS-Contract-Version") == "1.0.0")
    #expect(request.value(forHTTPHeaderField: "X-LifeOS-Device") == weekPlanDeviceID.uuidString.lowercased())
    #expect(request.value(forHTTPHeaderField: "X-LifeOS-Signature")?.isEmpty == false)
}

@Test("approval signing bytes match the Python canonical JSON vector")
func approvalSigningMatchesPythonCanonicalJSON() throws {
    let fields = [
        "actor": "33333333-3333-4333-8333-333333333333",
        "correlation_id": "c1c1c1c1-c1c1-c1c1-c1c1-c1c1c1c1c1c1",
        "device_id": "33333333-3333-4333-8333-333333333333",
        "expires_at": "2026-09-13T19:01:02+00:00",
        "fact_id": "f1111111-1111-4111-8111-111111111111",
        "idempotency_key": "week-plan-approve",
        "issued_at": "2026-09-13T18:56:02+00:00",
        "payload_hash": "abcd",
        "proof": "device_signed",
        "proposal_id": "p2222222-2222-4222-8222-222222222222",
    ]
    let expected = #"["lifeos.approval.v1","calendar.apply",{"actor":"33333333-3333-4333-8333-333333333333","correlation_id":"c1c1c1c1-c1c1-c1c1-c1c1-c1c1c1c1c1c1","device_id":"33333333-3333-4333-8333-333333333333","expires_at":"2026-09-13T19:01:02+00:00","fact_id":"f1111111-1111-4111-8111-111111111111","idempotency_key":"week-plan-approve","issued_at":"2026-09-13T18:56:02+00:00","payload_hash":"abcd","proof":"device_signed","proposal_id":"p2222222-2222-4222-8222-222222222222"}]"#

    let data = try LifeOSApprovalSigner.canonicalSigningData(actionClass: "calendar.apply", fields: fields)

    #expect(String(data: data, encoding: .ascii) == expected)
}

@Test("approval construction preserves one canonical payload across signatures")
func approvalConstructionIsIdempotent() throws {
    let key = try Curve25519.Signing.PrivateKey(rawRepresentation: Data(repeating: 0x42, count: 32))
    let signer = LifeOSApprovalSigner(signingKey: key)

    let first = try signer.approval(for: approvalInput(), actionClass: "calendar.apply")
    let second = try signer.approval(for: approvalInput(), actionClass: "calendar.apply")

    #expect(first.proof == .deviceSigned)
    #expect(first.issuedAt == "2026-09-13T18:56:02+00:00")
    #expect(first.expiresAt == "2026-09-13T19:01:02+00:00")
    #expect(first.signature.count == 128)
    #expect(try signer.signingData(for: first, actionClass: "calendar.apply")
        == signer.signingData(for: second, actionClass: "calendar.apply"))
    #expect(key.publicKey.isValidSignature(
        try #require(Data(hexadecimal: first.signature)),
        for: try signer.signingData(for: first, actionClass: "calendar.apply")
    ))
}

@Test("week-plan approval sends only the pre-built signed approval and decodes execution")
func approveWeekPlanContract() async throws {
    let response = Data(#"{"proposal_id":"22222222-2222-4222-8222-222222222222","state":"applied","lease_id":"55555555-5555-4555-8555-555555555555","applied_operations":1,"message":"Applied"}"#.utf8)
    let transport = WeekPlanningTransport(body: response)
    let relay = try makeWeekPlanningRelay(transport: transport)
    let approval = try LifeOSApprovalSigner(signingKey: Curve25519.Signing.PrivateKey())
        .approval(for: approvalInput(), actionClass: "calendar.apply")

    let result = try await relay.approveWeekPlan(proposalID: weekPlanProposalID, approval: approval)
    _ = try await relay.approveWeekPlan(proposalID: weekPlanProposalID, approval: approval)

    #expect(result.proposalID == weekPlanProposalID)
    #expect(result.state == .applied)
    #expect(result.appliedOperations == 1)
    let request = try #require(await transport.recordedRequest())
    #expect(request.url?.path == "/v1/week-plan/22222222-2222-4222-8222-222222222222/approve")
    #expect(request.httpMethod == "POST")
    let body = try #require(request.httpBody)
    let object = try #require(JSONSerialization.jsonObject(with: body) as? [String: Any])
    #expect(Set(object.keys) == ["approval"])
    let encodedApproval = try #require(object["approval"] as? [String: Any])
    #expect(encodedApproval["proposal_id"] as? String == weekPlanProposalID.uuidString.lowercased())
    #expect(encodedApproval["issued_at"] as? String == "2026-09-13T18:56:02+00:00")
    #expect(encodedApproval["signature"] as? String == approval.signature)
    let requests = await transport.recordedRequests()
    #expect(requests.count == 2)
    #expect(requests[0].httpBody == requests[1].httpBody)
}

@Test("week-plan payloads fail closed when a contract enum is unknown")
func malformedWeekPlanFailsClosed() async throws {
    let relay = try makeWeekPlanningRelay(transport: WeekPlanningTransport(body: weekPlanFixture(status: "maybe")))

    await #expect(throws: DecodingError.self) { try await relay.previewWeekPlan() }
}

@Test("week-plan relay preserves status, redirect, and response-size errors")
func weekPlanRelayPreservesRequestErrors() async throws {
    for status in [302, 409, 502] {
        let relay = try makeWeekPlanningRelay(transport: WeekPlanningTransport(status: status, body: Data()))
        await #expect(throws: ConversationRelayError.httpStatus(status)) { try await relay.previewWeekPlan() }
    }
    let oversized = Data(repeating: 0x20, count: 2 * 1024 * 1024 + 1)
    let relay = try makeWeekPlanningRelay(transport: WeekPlanningTransport(body: oversized))
    await #expect(throws: ConversationRelayError.responseTooLarge) { try await relay.previewWeekPlan() }
}

private extension Data {
    init?(hexadecimal: String) {
        guard hexadecimal.count.isMultiple(of: 2) else { return nil }
        var bytes: [UInt8] = []
        bytes.reserveCapacity(hexadecimal.count / 2)
        for index in stride(from: 0, to: hexadecimal.count, by: 2) {
            guard let byte = UInt8(hexadecimal.dropFirst(index).prefix(2), radix: 16) else { return nil }
            bytes.append(byte)
        }
        self.init(bytes)
    }
}
