import CryptoKit
import Foundation
import SecretaryClient
import Testing
@testable import SecretaryApp

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


private actor SessionTransport: RelayHTTPTransport {
    var requests: [URLRequest] = []
    var previewBody = weekPlanFixture()
    var approvalStatus = 200
    var malformedApproval = false
    var unreachable = false
    var suspended = false
    private var pending: CheckedContinuation<Void, Never>?

    func configure(status: Int = 200, malformed: Bool = false, unreachable: Bool = false, suspended: Bool = false) {
        approvalStatus = status
        previewBody = malformed ? Data("not JSON".utf8) : weekPlanFixture()
        self.unreachable = unreachable
        self.suspended = suspended
    }
    func corruptApproval() { malformedApproval = true }
    func clearOperations() throws {
        var object = try JSONSerialization.jsonObject(with: weekPlanFixture()) as! [String: Any]
        object["calendar_dry_run"] = ["calendar_summary": "LifeOS Proposed", "operations": []]
        previewBody = try JSONSerialization.data(withJSONObject: object)
    }
    func setConflict() {
        previewBody = Data(String(decoding: weekPlanFixture(), as: UTF8.self)
            .replacingOccurrences(of: "\"operation\":\"insert\"", with: "\"operation\":\"external_edit\"").utf8)
    }
    func send(_ request: URLRequest) async throws -> RelayHTTPResponse {
        requests.append(request)
        if suspended { await withCheckedContinuation { pending = $0 } }
        if unreachable { throw URLError(.cannotConnectToHost) }
        if request.url!.path.hasSuffix("/approve") {
            return RelayHTTPResponse(status: approvalStatus, body: malformedApproval ? Data("invalid".utf8) : Data(#"{"proposal_id":"22222222-2222-4222-8222-222222222222","state":"applied","lease_id":"55555555-5555-4555-8555-555555555555","applied_operations":1,"message":"Applied"}"#.utf8))
        }
        return RelayHTTPResponse(status: 200, body: previewBody)
    }
    func release() { suspended = false; pending?.resume(); pending = nil }
    func isWaiting() -> Bool { pending != nil }
    func recorded() -> [URLRequest] { requests }
}

@MainActor
private func makeSession(_ transport: SessionTransport, key: Curve25519.Signing.PrivateKey = .init()) throws -> ConversationSession {
    let identity = ConversationIdentity(conversationID: UUID(), participantID: UUID(), deviceID: UUID())
    return try ConversationSession(
        identity: identity,
        relay: SignedConversationRelay(origin: URL(string: "https://localhost:8443")!, deviceID: identity.deviceID,
                                       signingKey: key, transport: transport),
        approvalSigner: LifeOSApprovalSigner(signingKey: key),
        directory: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString),
        outboxKey: SymmetricKey(size: .bits256)
    )
}

@Test @MainActor func previewStoresExactProposal() async throws {
    let transport = SessionTransport()
    let session = try makeSession(transport)
    #expect(session.weekPlanState == .idle)
    await session.previewWeekPlan()
    #expect(session.weekPlanState == .proposed)
    let actual = try #require(session.weekPlanProposal)
    let decoder = JSONDecoder()
    decoder.dateDecodingStrategy = .iso8601
    let expected = try decoder.decode(WeekPlanProposal.self, from: weekPlanFixture())
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys]
    #expect(try encoder.encode(actual) == encoder.encode(expected))
}

@Test @MainActor func approvalSignsExactPreviewAndAppliesOnce() async throws {
    let transport = SessionTransport()
    let key = Curve25519.Signing.PrivateKey()
    let session = try makeSession(transport, key: key)
    await session.previewWeekPlan()
    let proposal = try #require(session.weekPlanProposal)
    let before = Date().addingTimeInterval(-1)
    await transport.configure(suspended: true)
    let task = Task { await session.approveWeekPlan() }
    while !(await transport.isWaiting()) { await Task.yield() }
    #expect(session.weekPlanState == .applying)
    await session.approveWeekPlan()
    await session.previewWeekPlan()
    await transport.release()
    await task.value
    await session.approveWeekPlan()
    #expect(session.weekPlanState == .applied)
    #expect(session.weekPlanResult?.appliedOperations == 1)
    let requests = await transport.recorded()
    #expect(requests.count == 2)
    struct Body: Decodable { let approval: LifeOSApproval }
    let approval = try JSONDecoder().decode(Body.self, from: #require(requests.last?.httpBody)).approval
    #expect(approval.proposalID == proposal.proposalID)
    #expect(approval.payloadHash == proposal.payloadHash)
    #expect(approval.actor == "user")
    #expect(approval.deviceID.uuidString.lowercased() == requests.last?.value(forHTTPHeaderField: "X-LifeOS-Device"))
    let formatter = ISO8601DateFormatter()
    let issued = try #require(formatter.date(from: approval.issuedAt))
    let expires = try #require(formatter.date(from: approval.expiresAt))
    #expect(issued >= before && issued <= Date())
    #expect(expires.timeIntervalSince(issued) == 300)
    #expect(approval.factID != approval.correlationID)
    #expect(UUID(uuidString: approval.idempotencyKey) != nil)
    let signature = Data(stride(from: 0, to: approval.signature.count, by: 2).map {
        UInt8(approval.signature.dropFirst($0).prefix(2), radix: 16)!
    })
    #expect(key.publicKey.isValidSignature(signature, for: try LifeOSApprovalSigner(signingKey: key)
        .signingData(for: approval, actionClass: "calendar.apply")))
    #expect(session.weekPlanProposal?.payloadHash == proposal.payloadHash)
}

@Test @MainActor func duplicatePreviewTapsAreCoalesced() async throws {
    let transport = SessionTransport()
    await transport.configure(suspended: true)
    let session = try makeSession(transport)
    let task = Task { await session.previewWeekPlan() }
    while !(await transport.isWaiting()) { await Task.yield() }
    #expect(session.weekPlanState == .planning)
    await session.previewWeekPlan()
    await transport.release()
    await task.value
    #expect(await transport.recorded().count == 1)
}

@Test(arguments: [401, 403, 404, 409, 422, 501, 502])
@MainActor func rejectedApprovalIsRecoverable(status: Int) async throws {
    let transport = SessionTransport()
    let session = try makeSession(transport)
    await session.previewWeekPlan()
    await transport.configure(status: status)
    await session.approveWeekPlan()
    guard case .recoverableError(let message) = session.weekPlanState else {
        Issue.record("Rejected approval must not succeed"); return
    }
    #expect(!message.isEmpty)
    #expect(session.weekPlanProposal != nil)
    #expect(session.weekPlanResult == nil)
    await session.approveWeekPlan()
    #expect(await transport.recorded().count == 2)
    await session.previewWeekPlan()
    #expect(session.weekPlanState == .proposed)
}

@Test(arguments: [false, true])
@MainActor func malformedOrUnreachablePreviewIsSafe(unreachable: Bool) async throws {
    let transport = SessionTransport()
    await transport.configure(malformed: !unreachable, unreachable: unreachable)
    let session = try makeSession(transport)
    await session.previewWeekPlan()
    guard case .recoverableError(let message) = session.weekPlanState else {
        Issue.record("Expected recoverable error"); return
    }
    #expect(message == "We couldn't load a complete plan from your Mac. Check your connection and try again.")
    #expect(session.weekPlanProposal == nil)
    #expect(session.weekPlanResult == nil)
    await transport.configure()
    await session.previewWeekPlan()
    #expect(session.weekPlanState == .proposed)
}

@Test @MainActor func lostApplyResponseNeverClaimsSuccessOrRetries() async throws {
    let transport = SessionTransport()
    let session = try makeSession(transport)
    await session.previewWeekPlan()
    await transport.configure(unreachable: true)
    await session.approveWeekPlan()
    guard case .recoverableError(let message) = session.weekPlanState else {
        Issue.record("Expected uncertain application outcome"); return
    }
    #expect(message.contains("Some changes may already be there"))
    #expect(session.weekPlanResult == nil)
    await session.approveWeekPlan()
    #expect(await transport.recorded().count == 2)
}

@Test @MainActor func malformedApplyResponseIsRecoverable() async throws {
    let transport = SessionTransport()
    let session = try makeSession(transport)
    await session.previewWeekPlan()
    await transport.corruptApproval()
    await session.approveWeekPlan()
    guard case .recoverableError = session.weekPlanState else {
        Issue.record("Malformed result must not show success"); return
    }
    #expect(session.weekPlanResult == nil)
}

@Test @MainActor func externalEditsRequireReviewBeforeApproval() async throws {
    let transport = SessionTransport()
    await transport.setConflict()
    let session = try makeSession(transport)
    await session.previewWeekPlan()
    #expect(session.weekPlanState == .proposed)
    #expect(!session.canApproveWeekPlan)
    await session.approveWeekPlan()
    #expect(await transport.recorded().count == 1)
}

@Test @MainActor func newReviewUsesFreshApprovalIdentifiers() async throws {
    let transport = SessionTransport()
    let session = try makeSession(transport)
    await session.previewWeekPlan()
    await transport.configure(status: 409)
    await session.approveWeekPlan()
    await session.previewWeekPlan()
    await session.approveWeekPlan()
    struct Body: Decodable { let approval: LifeOSApproval }
    let approvals = try await transport.recorded().filter { $0.url!.path.hasSuffix("/approve") }.map {
        try JSONDecoder().decode(Body.self, from: $0.httpBody!).approval
    }
    #expect(approvals.count == 2)
    #expect(approvals[0].factID != approvals[1].factID)
    #expect(approvals[0].correlationID != approvals[1].correlationID)
    #expect(approvals[0].idempotencyKey != approvals[1].idempotencyKey)
}

@Test @MainActor func emptyPreviewCannotClaimCalendarApplication() async throws {
    let transport = SessionTransport()
    try await transport.clearOperations()
    let session = try makeSession(transport)
    await session.previewWeekPlan()
    #expect(!session.canApproveWeekPlan)
    await session.approveWeekPlan()
    #expect(session.weekPlanState == .proposed)
    #expect(session.weekPlanResult == nil)
    #expect(await transport.recorded().count == 1)
}
