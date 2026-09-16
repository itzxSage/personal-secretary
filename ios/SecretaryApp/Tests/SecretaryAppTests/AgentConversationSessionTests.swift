import CryptoKit
import Foundation
import SecretaryClient
import Testing
@testable import SecretaryApp

private actor AgentSessionTransport: RelayHTTPTransport {
    let identity: ConversationIdentity
    let delayed: Bool
    let action: String?
    let createConflict: Bool
    var requests: [URLRequest] = []
    init(identity: ConversationIdentity, delayed: Bool = false, action: String? = nil,
         createConflict: Bool = false) {
        self.identity = identity; self.delayed = delayed; self.action = action
        self.createConflict = createConflict
    }
    func send(_ request: URLRequest) async throws -> RelayHTTPResponse {
        requests.append(request)
        if request.url!.path == "/v1/conversations" {
            if createConflict {
                return RelayHTTPResponse(status: 409, body: try JSONSerialization.data(withJSONObject: [
                    "detail": "conversation conflict",
                ]))
            }
            return RelayHTTPResponse(status: 200, body: try JSONSerialization.data(withJSONObject: [
                "conversation_id": identity.conversationID.uuidString, "title": "LifeOS",
                "participant_ids": [identity.participantID.uuidString], "device_ids": [identity.deviceID.uuidString],
                "created_at": "2026-09-15T00:00:00Z", "state": "active",
            ]))
        }
        if delayed {
            // Deliberately ignore cancellation, as a server may complete after the client cancels.
            try? await Task.sleep(for: .milliseconds(120))
        }
        let actions: [[String: Any]] = action.map { [["action_class": $0, "payload": ["command": "arbitrary shell"]]] } ?? []
        return RelayHTTPResponse(status: 200, body: try JSONSerialization.data(withJSONObject: [
            "reply_text": "Tomorrow has room for a break.", "proposed_actions": actions,
        ]))
    }
    func recorded() -> [URLRequest] { requests }
}

@MainActor private func agentSession(delayed: Bool = false, action: String? = nil,
    createConflict: Bool = false) throws
    -> (ConversationSession, AgentSessionTransport, URL) {
    let identity = ConversationIdentity(conversationID: UUID(), participantID: UUID(), deviceID: UUID())
    let transport = AgentSessionTransport(identity: identity, delayed: delayed, action: action,
                                          createConflict: createConflict)
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    let relay = try SignedConversationRelay(origin: URL(string: "https://localhost:8443")!, deviceID: identity.deviceID,
                                             signingKey: Curve25519.Signing.PrivateKey(), transport: transport)
    return (ConversationSession(identity: identity, relay: relay, approvalSigner: nil,
                                directory: directory, outboxKey: SymmetricKey(size: .bits256)), transport, directory)
}

@Test @MainActor func followupsReuseConversationAndRenderRealReplies() async throws {
    let (session, transport, directory) = try agentSession()
    defer { try? FileManager.default.removeItem(at: directory) }
    #expect(await session.sendAgentTurn("What does tomorrow look like?"))
    #expect(await session.sendAgentTurn("Give me another hour to sleep."))
    let turns = await transport.recorded().filter { $0.url!.path == "/v1/conversation/turn" }
    #expect(turns.count == 2)
    let first = try JSONSerialization.jsonObject(with: turns[0].httpBody!) as! [String: String]
    let second = try JSONSerialization.jsonObject(with: turns[1].httpBody!) as! [String: String]
    #expect(first["conversation_id"] == second["conversation_id"])
    #expect(first["text"] != second["text"])
    #expect(session.agentMessages.map(\.role) == ["You", "LifeOS", "You", "LifeOS"])
    #expect(session.agentMessages.last?.text == "Tomorrow has room for a break.")
}

@Test @MainActor func cancelledAgentReplyNeverReachesUser() async throws {
    let (session, transport, directory) = try agentSession(delayed: true)
    defer { try? FileManager.default.removeItem(at: directory) }
    let pending = Task { await session.sendAgentTurn("What does tomorrow look like?") }
    while await transport.recorded().count < 2 { await Task.yield() }
    session.cancelAgentTurn()
    #expect(await pending.value == false)
    #expect(session.agentMessages.map(\.role) == ["You"])
    #expect(!session.isAgentProcessing)
}

@Test @MainActor func arbitraryAgentActionFailsClosedWithoutDispatch() async throws {
    let (session, transport, directory) = try agentSession(action: "code.execute")
    defer { try? FileManager.default.removeItem(at: directory) }
    #expect(await session.sendAgentTurn("Do something") == false)
    let requests = await transport.recorded()
    #expect(requests.map { $0.url!.path } == ["/v1/conversations", "/v1/conversation/turn"])
    #expect(session.agentMessages.last?.text.contains("couldn't get a reply") == true)
}

@Test @MainActor func spokenApprovalNeverCallsCalendarExecution() async throws {
    let (session, transport, directory) = try agentSession(action: "approval_required")
    defer { try? FileManager.default.removeItem(at: directory) }
    #expect(await session.sendAgentTurn("Okay make that change"))
    #expect(await transport.recorded().count == 2)
    #expect(session.weekPlanState == .idle)
    #expect(session.agentMessages.last?.text.contains("tap Approve") == true)
}

@Test @MainActor func preExistingConversationDoesNotBlockTurn() async throws {
    // synchronize() creates the conversation under "Secretary"; sendAgentTurn()
    // re-creates it under "LifeOS". The relay 409s on the title mismatch, but the
    // conversation already exists, so the turn must still proceed and render.
    let (session, transport, directory) = try agentSession(createConflict: true)
    defer { try? FileManager.default.removeItem(at: directory) }
    #expect(await session.sendAgentTurn("Help me plan my week") == true)
    #expect(session.agentMessages.map(\.role) == ["You", "LifeOS"])
    #expect(session.agentMessages.last?.text == "Tomorrow has room for a break.")
}
