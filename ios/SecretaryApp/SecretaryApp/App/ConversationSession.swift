import CryptoKit
import Foundation
import Observation
import SecretaryClient
import SecretaryContract
#if os(iOS)
import LocalAuthentication
import UIKit
#endif

/// App-wide conversation state. Every invocation surface routes through
/// `InvocationAdapter` into `StartVoiceSession`; the encrypted outbox is the
/// event sink, so offline events persist without plaintext at rest.
@Observable
@MainActor
final class ConversationSession {
    static let shared: ConversationSession = {
        do {
            return try ConversationSession()
        } catch {
            preconditionFailure("Encrypted outbox key unavailable")
        }
    }()

    private let adapter: InvocationAdapter
    private let router: DeepLinkRouter
    private let outbox: EncryptedOutbox
    private let cache: EncryptedConversationCache
    private let pendingWeekPlan: EncryptedPendingWeekPlan
    private(set) var weekPlanNeedsRecovery = false
    private let sendTextMessage: SendTextMessage
    private let endVoiceSessionCommand: EndVoiceSession
    private let identity: ConversationIdentity
    private let relay: SignedConversationRelay?
    private let approvalSigner: (any LifeOSApprovalSigning)?

    enum WeekPlanState: Equatable {
        case idle, planning, proposed, applying, applied
        case recoverableError(String)
    }
    private(set) var weekPlanState: WeekPlanState = .idle
    private(set) var weekPlanProposal: WeekPlanProposal?
    private(set) var weekPlanResult: WeekPlanExecutionResult?
    var canApproveWeekPlan: Bool {
        guard weekPlanState == .proposed, let proposal = weekPlanProposal else { return false }
        return !proposal.calendarDryRun.operations.isEmpty && proposal.calendarDryRun.operations.allSatisfy {
            $0.operation == .insert || $0.operation == .update || $0.operation == .noop
        }
    }
    var isWeekPlanBusy: Bool { weekPlanState == .planning || weekPlanState == .applying }

    private(set) var isBusy = false
    private(set) var hasDeliveryConflict = false
    var isRelayConfigured: Bool { relay != nil }
    private var nextSequence = 1
    private(set) var transcript: [ConversationEvent] = []
    private(set) var statusMessage: String?
    private(set) var interviewReply: LifeInterviewReply?
    private(set) var interviewError: String?
    private(set) var isSessionActive = false
    private var activeTurnID: UUID?
    struct AgentMessage: Identifiable {
        let id = UUID()
        let role: String
        let text: String
    }
    private(set) var agentMessages: [AgentMessage] = []
    private(set) var isAgentProcessing = false
    private(set) var showAgentWeekPlan = false
    private var agentTask: Task<AgentTurnReply, Error>?
    private var agentGeneration = 0
    private var pendingInvocationSource: InvocationSource?
#if os(iOS)
    let voice = NativeInterviewVoice()
#endif

#if DEBUG && os(iOS)
    private var didRunRelaySmokeTest = false
    private var didRunRelaySmokeCleanup = false
    private var relayDiagnostic = "not attempted"
#endif

    convenience init() throws {
        let configuration = try RelayConnectionConfiguration.load()
        let identity = configuration?.identity ?? ConversationIdentity(
            conversationID: UUID(uuidString: "55555555-5555-4555-8555-555555555555")!,
            participantID: UUID(uuidString: "11111111-1111-4111-8111-111111111111")!,
            deviceID: UUID(uuidString: "33333333-3333-4333-8333-333333333333")!
        )
        try self.init(
            identity: identity, relay: configuration?.client(),
            approvalSigner: configuration?.approvalSigner(),
            directory: Self.outboxDirectory(), outboxKey: OutboxKeyStore.shared.key()
        )
    }

    init(
        identity: ConversationIdentity, relay: SignedConversationRelay?,
        approvalSigner: (any LifeOSApprovalSigning)?, directory: URL, outboxKey: SymmetricKey
    ) {
        self.identity = identity
        self.relay = relay
        self.approvalSigner = approvalSigner
        let outbox = EncryptedOutbox(directory: directory, key: outboxKey)
        self.outbox = outbox
        self.cache = EncryptedConversationCache(directory: directory, key: outboxKey)
        self.pendingWeekPlan = EncryptedPendingWeekPlan(directory: directory, key: outboxKey)
        self.sendTextMessage = SendTextMessage(identity: identity, sink: outbox)
        self.endVoiceSessionCommand = EndVoiceSession(identity: identity, sink: outbox)
        let command = StartVoiceSession(
            identity: identity,
            permission: MicrophoneAuthorizationProvider(),
            sink: outbox
        )
        self.adapter = InvocationAdapter(startVoiceSession: command)
        self.router = DeepLinkRouter(adapter: adapter)
    }

    func previewWeekPlan() async {
        guard !isWeekPlanBusy, !weekPlanNeedsRecovery else { return }
        guard let relay else {
            weekPlanState = .recoverableError("Connect to your Life Engine on your Mac, then try again.")
            return
        }
        weekPlanState = .planning
        weekPlanResult = nil
        do {
            let proposal = try await relay.previewWeekPlan()
            guard proposal.windowEnd > proposal.windowStart,
                TimeZone(identifier: proposal.timezone) != nil,
                !proposal.payloadHash.isEmpty else { throw ConversationRelayError.invalidResponse }
            weekPlanProposal = proposal
            weekPlanState = .proposed
        } catch {
            weekPlanState = .recoverableError(Self.weekPlanError(error, applying: false))
        }
    }

    func approveWeekPlan() async {
        // Only an explicit approval of the currently displayed preview may execute.
        // Errors require a new preview and a new review, never an automatic replay.
        guard canApproveWeekPlan, let proposal = weekPlanProposal else { return }
        guard let relay, let approvalSigner else {
            weekPlanState = .recoverableError("Reconnect this iPhone to your Life Engine before approving.")
            return
        }
        weekPlanState = .applying
        do {
#if os(iOS)
            let authentication = LAContext()
            _ = try await authentication.evaluatePolicy(
                .deviceOwnerAuthentication, localizedReason: "Approve the calendar changes you reviewed"
            )
#endif
            let now = Date()
            let approval = try approvalSigner.approval(for: LifeOSApprovalInput(
                factID: UUID(), proposalID: proposal.proposalID, payloadHash: proposal.payloadHash,
                issuedAt: now, expiresAt: now.addingTimeInterval(300),
                idempotencyKey: UUID().uuidString.lowercased(), actor: "user",
                correlationID: UUID(), deviceID: identity.deviceID
            ), actionClass: "calendar.apply")
            try await pendingWeekPlan.save(proposal)
            weekPlanNeedsRecovery = true
            let result = try await relay.approveWeekPlan(proposalID: proposal.proposalID, approval: approval)
            guard result.appliedOperations >= 0 else { throw ConversationRelayError.invalidResponse }
            weekPlanResult = result
            try await pendingWeekPlan.clear()
            weekPlanNeedsRecovery = false
            weekPlanState = .applied
            publishAssistant("Your LifeOS calendar is updated. Completed \(result.appliedOperations) changes.")
        } catch {
            if Self.approvalFailureCannotHaveApplied(error) {
                try? await pendingWeekPlan.clear()
                weekPlanNeedsRecovery = false
            }
            let message = Self.weekPlanError(error, applying: true)
            weekPlanState = .recoverableError(message)
            publishAssistant(message)
        }
    }

    func recoverWeekPlan() async {
        guard !isWeekPlanBusy, let proposal = weekPlanProposal,
              let relay, let approvalSigner else { return }
        weekPlanState = .applying
        do {
#if os(iOS)
            _ = try await LAContext().evaluatePolicy(
                .deviceOwnerAuthentication, localizedReason: "Recover only the calendar changes you already approved"
            )
#endif
            let now = Date()
            let approval = try approvalSigner.recoveryApproval(for: LifeOSApprovalInput(
                factID: UUID(), proposalID: proposal.proposalID, payloadHash: proposal.payloadHash,
                issuedAt: now, expiresAt: now.addingTimeInterval(300),
                idempotencyKey: UUID().uuidString.lowercased(), actor: "user",
                correlationID: UUID(), deviceID: identity.deviceID
            ))
            let result = try await relay.recoverWeekPlan(proposalID: proposal.proposalID, approval: approval)
            switch result.outcome {
            case .applied:
                guard result.state == "applied", result.missingEvents.isEmpty else {
                    throw ConversationRelayError.invalidResponse
                }
                try await pendingWeekPlan.clear()
                weekPlanNeedsRecovery = false
                weekPlanState = .applied
                publishAssistant("Recovery confirmed your approved LifeOS calendar changes are complete.")
            case .reset:
                guard result.state == "proposed", result.succeededEvents.isEmpty else {
                    throw ConversationRelayError.invalidResponse
                }
                try await pendingWeekPlan.clear()
                weekPlanNeedsRecovery = false
                weekPlanState = .proposed
                publishAssistant("No calendar changes were found. Review this same plan and approve it again to apply it.")
            case .partial, .conflict:
                let message = "Calendar recovery is incomplete. Some events may exist. Review your LifeOS calendar before trying recovery again."
                weekPlanState = .recoverableError(message)
                publishAssistant(message)
            }
        } catch {
            let message = "Calendar recovery could not be confirmed. Keep this proposal and try recovery again; some changes may already exist."
            weekPlanState = .recoverableError(message)
            publishAssistant(message)
        }
    }

    private static func weekPlanError(_ error: Error, applying: Bool) -> String {
        switch error {
        case ConversationRelayError.httpStatus(409):
            return "This plan can no longer be applied as shown. Use Recover calendar changes to reconcile this same proposal."
        case ConversationRelayError.httpStatus(401), ConversationRelayError.httpStatus(403):
            return "Reconnect this iPhone to your Life Engine, then create a fresh preview."
        case ConversationRelayError.httpStatus(501):
            return "Week planning isn't ready on your Mac yet. Finish connecting Calendar, then try again."
        case ConversationRelayError.httpStatus(404):
            return "This plan is no longer available. Create a fresh preview to continue."
        default:
            return applying
                ? "We couldn't confirm that your week was applied. Use Recover calendar changes for this same proposal. Some changes may already be there."
                : "We couldn't load a complete plan from your Mac. Check your connection and try again."
        }
    }

    private static func approvalFailureCannotHaveApplied(_ error: Error) -> Bool {
        switch error {
        case ConversationRelayError.httpStatus(401), ConversationRelayError.httpStatus(403),
             ConversationRelayError.httpStatus(404), ConversationRelayError.httpStatus(409),
             ConversationRelayError.httpStatus(422), ConversationRelayError.httpStatus(501):
            return true
        default:
            return false
        }
    }

    func loadInterview() async {
        guard !isBusy else { return }
        guard let relay else {
            interviewError = "Connect to your Life Engine to start the interview."
            return
        }
        isBusy = true
        defer { isBusy = false }
        do {
            interviewReply = try await relay.startInterview()
            interviewError = nil
        } catch { interviewError = "I couldn't reach your Life Engine. Your saved answers are safe." }
    }

    func advanceInterview(_ action: LifeInterviewAction, text: String = "") async -> Bool {
        guard !isBusy, let relay, let reply = interviewReply else { return false }
        isBusy = true
        defer { isBusy = false }
        do {
            interviewReply = try await relay.interviewTurn(
                revision: reply.revision, action: action, text: text, questionKey: reply.question?.key
            )
            interviewError = nil
            return true
        } catch ConversationRelayError.httpStatus(409) {
            interviewError = "The interview changed on another device. Resume before sending again."
        } catch {
            interviewError = "I couldn't save that answer. Keep it here and try again."
        }
        return false
    }

    func resumeForegroundInvocation() async {
        guard let source = pendingInvocationSource else { return }
        pendingInvocationSource = nil
        await startVoiceSession(source: source)
    }

    func startVoiceSession(source: InvocationSource) async {
#if os(iOS)
        guard UIApplication.shared.applicationState == .active else {
            pendingInvocationSource = source
            return
        }
#endif
        guard !isBusy, !isSessionActive, !hasDeliveryConflict else { return }
        isBusy = true
        defer { isBusy = false }
        guard await refreshSequence() else { return }
        let values = InvocationValues(
            eventID: UUID(),
            turnID: UUID(),
            occurredAt: Date(),
            sequence: nextSequence
        )
        let outcome = await adapter.invoke(source, values: values)
        apply(outcome)
        if case .started = outcome {
            DeviceSurfaceReceiptStore.record(source)
            await beginNativeVoice()
        }
    }

    func handleDeepLink(_ url: URL) async {
        guard !isBusy, !isSessionActive, !hasDeliveryConflict else { return }
        isBusy = true
        defer { isBusy = false }
        guard await refreshSequence() else { return }
        let values = InvocationValues(
            eventID: UUID(),
            turnID: UUID(),
            occurredAt: Date(),
            sequence: nextSequence
        )
        let outcome = await router.handle(url, values: values)
        apply(outcome)
        if case .started = outcome,
            let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
            let rawSource = components.queryItems?.first(where: { $0.name == "source" })?.value,
            let source = InvocationSource(rawValue: rawSource) {
            DeviceSurfaceReceiptStore.record(source)
            await beginNativeVoice()
        }
    }

    func endVoiceSession() async {
        // Stop microphone and pending replies immediately, including during a network call.
        cancelAgentTurn()
#if os(iOS)
        voice.pause()
#endif
        guard isSessionActive, let activeTurnID else { return }
        isSessionActive = false
        self.activeTurnID = nil
        statusMessage = "Voice session ended"
        guard !hasDeliveryConflict, await refreshSequence() else { return }
        do {
            let event = try await endVoiceSessionCommand.execute(values: InvocationValues(
                eventID: UUID(), turnID: activeTurnID, occurredAt: Date(), sequence: nextSequence
            ))
            transcript.append(event)
            nextSequence = event.sequence + 1
        } catch { statusMessage = "Voice stopped. The end marker could not be saved." }
    }

    private func beginNativeVoice() async {
#if os(iOS)
        voice.onAnswer = { [weak self] text in
            Task { @MainActor [weak self] in _ = await self?.sendAgentTurn(text) }
        }
        await voice.start(question: "")
        if !voice.conversationEnabled {
            isSessionActive = false
            statusMessage = voice.errorMessage
        }
#endif
    }

    func cancelAgentTurn() {
        agentGeneration += 1
        agentTask?.cancel()
        agentTask = nil
        isAgentProcessing = false
    }

    func interruptVoice() {
        cancelAgentTurn()
#if os(iOS)
        voice.interruptAndListen()
#endif
    }

    func dismissAgentWeekPlan() { showAgentWeekPlan = false }

    private func publishAssistant(_ text: String) {
        agentMessages.append(AgentMessage(role: "LifeOS", text: text))
        statusMessage = text
#if os(iOS)
        if isSessionActive { voice.speak(text) }
#endif
    }

    @discardableResult
    func sendAgentTurn(_ text: String) async -> Bool {
        guard !isAgentProcessing else { return false }
        let text = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, text.utf8.count <= 16_000 else {
            statusMessage = "Enter a message under 16,000 bytes."
            return false
        }
        guard let relay else {
            publishAssistant("Connect this iPhone to your Life Engine to talk with LifeOS.")
            return false
        }
        agentGeneration += 1
        let generation = agentGeneration
        isAgentProcessing = true
        agentMessages.append(AgentMessage(role: "You", text: text))
        statusMessage = "Thinking…"
        let task = Task {
            // The conversation may already exist under a title established by
            // synchronize() ("Secretary"). The relay rejects a title mismatch with
            // HTTP 409, but a turn only requires the conversation to exist, so a
            // 409 from create is safe to ignore and we proceed to the turn.
            do {
                _ = try await relay.create(conversationID: identity.conversationID, title: "LifeOS")
            } catch ConversationRelayError.httpStatus(409) {
                // Existing conversation (possibly a different title); resume to turn.
            }
            try Task.checkCancellation()
            return try await relay.conversationTurn(conversationID: identity.conversationID, text: text)
        }
        agentTask = task
        defer {
            if agentGeneration == generation { isAgentProcessing = false; agentTask = nil }
        }
        do {
            let reply = try await task.value
            guard generation == agentGeneration, !Task.isCancelled else { return false }
            if reply.proposedActions.contains(where: { $0.actionClass == .weekPlanPreview }) {
                await previewWeekPlan()
                guard generation == agentGeneration else { return false }
                showAgentWeekPlan = true
                switch weekPlanState {
                case .proposed:
                    publishAssistant(reply.replyText + " Review the schedule on screen. Calendar changes require your explicit approval.")
                case .recoverableError(let message): publishAssistant(message)
                default: publishAssistant("The plan is not ready to review yet.")
                }
            } else if reply.proposedActions.contains(where: { $0.actionClass == .approvalRequired }) {
                showAgentWeekPlan = weekPlanProposal != nil
                publishAssistant("Review your current plan and tap Approve calendar changes. A spoken request cannot authorize Calendar writes.")
            } else { publishAssistant(reply.replyText) }
            return true
        } catch ConversationRelayError.httpStatus(401), ConversationRelayError.httpStatus(403) {
            publishAssistant("This iPhone is not authorized by the Life Engine. No calendar action was authorized.")
            return false
        } catch ConversationRelayError.httpStatus(502) {
            publishAssistant("The Life Engine reached Hermes, but Hermes could not provide a reply. No calendar action was authorized.")
            return false
        } catch ConversationRelayError.httpStatus(503) {
            publishAssistant("Hermes is temporarily unavailable. Try again shortly. No calendar action was authorized.")
            return false
        } catch {
            guard generation == agentGeneration, !Task.isCancelled else { return false }
            publishAssistant("I couldn't get a reply from your Life Engine. Check the connection and try again. No calendar action was authorized.")
            return false
        }
    }

    func sendText(_ text: String) async -> Bool {
        guard !isBusy, !hasDeliveryConflict else { return false }
        isBusy = true
        defer { isBusy = false }
        guard await refreshSequence() else { return false }
        guard !isSessionActive else {
            statusMessage = "Finish the voice session before sending text"
            return false
        }
        do {
            let request = try TextMessageRequest(
                text: text,
                identifiers: TextMessageIdentifiers(
                    partialEventID: UUID(),
                    finalEventID: UUID(),
                    turnID: UUID()
                ),
                position: TextMessagePosition(
                    occurredAt: Date(),
                    startingSequence: nextSequence
                )
            )
            let events = try await sendTextMessage.execute(request)
            transcript.append(contentsOf: events)
            nextSequence += events.count
            statusMessage = "Message saved for delivery"
            return true
        } catch TextMessageError.emptyMessage {
            statusMessage = "Type a message before sending"
            return false
        } catch TextMessageError.messageTooLarge {
            statusMessage = "Message is too long. Split it into shorter messages."
            return false
        } catch {
            statusMessage = "Message could not be saved"
            return false
        }
    }

    func loadPendingEvents() async {
        guard !isBusy else { return }
        isBusy = true
        defer { isBusy = false }
        do {
            let pending = try await outbox.pendingEvents()
            let delivered = try await cache.events(conversationID: identity.conversationID)
            if let interrupted = try await pendingWeekPlan.load() {
                weekPlanProposal = interrupted
                weekPlanNeedsRecovery = true
                weekPlanState = .recoverableError("A calendar operation was interrupted. Recover this same plan before creating another.")
            }
            let deliveredIDs = Set(delivered.map(\.eventId))
            transcript = delivered + pending.filter { !deliveredIDs.contains($0.eventId) }
            // Restored transcript markers never restart a microphone session.
            nextSequence = try await outbox.nextSequence(conversationID: identity.conversationID)
            if relay == nil { statusMessage = "Local demo — relay not configured" }
        } catch {
            statusMessage = "Offline messages are unavailable"
        }
    }

#if DEBUG && os(iOS)
    func runRelaySmokeTestIfRequested() async {
        guard ProcessInfo.processInfo.environment["LIFEOS_RELAY_SMOKE_TEST"] == "1",
            !didRunRelaySmokeTest, relay != nil else { return }
        didRunRelaySmokeTest = true
        if await sendText("Private relay pairing verified") {
            await synchronize()
        }
        DeveloperRelayPairing.writeSmokeTestResult(
            status: statusMessage ?? "No status",
            diagnostic: relayDiagnostic
        )
    }

    func runRelaySmokeCleanupIfRequested() async {
        guard ProcessInfo.processInfo.environment["LIFEOS_RELAY_SMOKE_CLEANUP"] == "1",
            !didRunRelaySmokeCleanup, relay != nil else { return }
        didRunRelaySmokeCleanup = true
        await deleteConversation()
        DeveloperRelayPairing.writeSmokeTestResult(
            status: statusMessage ?? "No status",
            diagnostic: "cleanup"
        )
    }
#endif

    func synchronize() async {
        guard !isBusy, let relay else { return }
        isBusy = true
        defer { isBusy = false }
        guard await refreshSequence() else { return }
        do {
            let conversation = try await relay.create(conversationID: identity.conversationID, title: "Secretary")
            guard conversation.participantIds.contains(identity.participantID) else {
                throw ConversationRelayError.invalidResponse
            }
            var remote: [ConversationEvent] = []
            var cursor = 0
            while true {
                let page = try await relay.resume(conversationID: identity.conversationID, after: cursor)
                remote.append(contentsOf: page.events)
                cursor = page.cursor
                if page.events.count < 100 { break }
            }
            let pending = try await outbox.pendingEvents()
            let remoteIDs = Set(remote.map(\.eventId))
            transcript = remote + pending.filter { !remoteIDs.contains($0.eventId) }
            while let acknowledgment = try await relay.flush(outbox) {
                nextSequence = max(nextSequence, acknowledgment.nextSequence)
            }
            try await cache.replace(conversationID: identity.conversationID, events: transcript)
            nextSequence = max(nextSequence, cursor + 1)
            hasDeliveryConflict = false
            statusMessage = "Conversation saved to Mac."
#if DEBUG && os(iOS)
            relayDiagnostic = "success"
#endif
        } catch ConversationRelayError.httpStatus(409) {
            hasDeliveryConflict = true
            statusMessage = "Delivery conflict. Queued messages kept; review required."
        } catch {
            statusMessage = "Mac unavailable or setup incomplete. Queued messages kept."
#if DEBUG && os(iOS)
            relayDiagnostic = String(reflecting: error)
#endif
        }
    }

    func deleteConversation() async {
        guard !isBusy, let relay else { return }
        isBusy = true
        defer { isBusy = false }
        cancelAgentTurn()
#if os(iOS)
        voice.pause()
#endif
        isSessionActive = false
        do {
            try await relay.deleteConversation(identity.conversationID)
            try await outbox.purge(conversationID: identity.conversationID)
            try await cache.purge(conversationID: identity.conversationID)
            cancelAgentTurn()
            agentMessages = []
            transcript = []
            nextSequence = 1
            hasDeliveryConflict = false
            statusMessage = "Conversation deleted from this device and Mac."
        } catch {
            statusMessage = "Deletion failed. Local messages were kept."
        }
    }

    private func refreshSequence() async -> Bool {
        do {
            let pending = try await outbox.pendingEvents()
            guard pending.allSatisfy({
                $0.conversationId == identity.conversationID
                    && $0.participantId == identity.participantID && $0.deviceId == identity.deviceID
            }) else {
                statusMessage = "Outbox belongs to another setup. Pending messages kept."
                return false
            }
            nextSequence = max(nextSequence, try await outbox.nextSequence(conversationID: identity.conversationID))
            return true
        } catch {
            statusMessage = "Offline messages are unavailable"
            return false
        }
    }

    private func apply(_ outcome: InvocationOutcome) {
        switch outcome {
        case .started(let event):
            transcript.append(event)
            nextSequence = event.sequence + 1
            isSessionActive = true
            if case .transcriptPartial(let payload) = event.payload {
                activeTurnID = payload.turnId
            }
            statusMessage = "Voice session started"
        case .permissionDenied:
            isSessionActive = false
            statusMessage = "Microphone permission denied. Text conversation is available."
        case .invalidInvocation:
            statusMessage = "Invalid invocation"
        }
    }

    private func restoreVoiceSessionState() {
        for event in transcript.reversed() {
            switch event.payload {
            case .transcriptPartial(let payload) where payload.text == "voice_session_started":
                isSessionActive = true
                activeTurnID = payload.turnId
                return
            case .transcriptFinal, .cancellation:
                isSessionActive = false
                activeTurnID = nil
                return
            case .transcriptPartial, .attachment, .toolResult, .resume, .error:
                continue
            }
        }
        isSessionActive = false
        activeTurnID = nil
    }

    private static func outboxDirectory() -> URL {
        let base = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first ?? FileManager.default.temporaryDirectory
        return base.appendingPathComponent("Secretary/Outbox", isDirectory: true)
    }
}
