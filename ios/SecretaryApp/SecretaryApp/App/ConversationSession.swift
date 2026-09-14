import Foundation
import Observation
import SecretaryClient
import SecretaryContract

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
    private let sendTextMessage: SendTextMessage
    private let endVoiceSessionCommand: EndVoiceSession
    private let identity: ConversationIdentity
    private let relay: SignedConversationRelay?
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
#if DEBUG && os(iOS)
    private var didRunRelaySmokeTest = false
    private var didRunRelaySmokeCleanup = false
    private var relayDiagnostic = "not attempted"
#endif

    init() throws {
        let configuration = try RelayConnectionConfiguration.load()
        let identity = configuration?.identity ?? ConversationIdentity(
            conversationID: UUID(uuidString: "55555555-5555-4555-8555-555555555555")!,
            participantID: UUID(uuidString: "11111111-1111-4111-8111-111111111111")!,
            deviceID: UUID(uuidString: "33333333-3333-4333-8333-333333333333")!
        )
        self.identity = identity
        self.relay = try configuration?.client()
        let directory = Self.outboxDirectory()
        let outboxKey = try OutboxKeyStore.shared.key()
        let outbox = EncryptedOutbox(directory: directory, key: outboxKey)
        self.outbox = outbox
        self.cache = EncryptedConversationCache(directory: directory, key: outboxKey)
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

    func startVoiceSession(source: InvocationSource) async {
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
        }
    }

    func endVoiceSession() async {
        guard !isBusy, isSessionActive, let activeTurnID, !hasDeliveryConflict else { return }
        isBusy = true
        defer { isBusy = false }
        guard await refreshSequence() else { return }
        do {
            let event = try await endVoiceSessionCommand.execute(
                values: InvocationValues(
                    eventID: UUID(),
                    turnID: activeTurnID,
                    occurredAt: Date(),
                    sequence: nextSequence
                )
            )
            transcript.append(event)
            nextSequence = event.sequence + 1
            isSessionActive = false
            self.activeTurnID = nil
            statusMessage = "Voice session ended"
        } catch {
            statusMessage = "Voice session could not be ended"
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
            let deliveredIDs = Set(delivered.map(\.eventId))
            transcript = delivered + pending.filter { !deliveredIDs.contains($0.eventId) }
            restoreVoiceSessionState()
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
            isSessionActive = false
            activeTurnID = nil
            statusMessage = "Saved to Mac. Assistant replies are not connected yet."
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
        do {
            try await relay.deleteConversation(identity.conversationID)
            try await outbox.purge(conversationID: identity.conversationID)
            try await cache.purge(conversationID: identity.conversationID)
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
