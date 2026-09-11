#if os(iOS)
import AppIntents
import SecretaryClient

struct StartVoiceSessionIntent: AppIntent {
    static let title: LocalizedStringResource = "Start Voice Session"
    static let description = IntentDescription("Starts a voice conversation with Secretary.")
    static let openAppWhenRun = true

    @MainActor
    func perform() async throws -> some IntentResult {
        await ConversationSession.shared.startVoiceSession(source: .appShortcut)
        return .result()
    }
}

@available(iOS 18.0, *)
struct StartControlCenterVoiceSessionIntent: AppIntent {
    static let title: LocalizedStringResource = "Start Voice Session"
    static let description = IntentDescription("Opens Secretary and starts a voice session.")
    static let openAppWhenRun = true

    @MainActor
    func perform() async throws -> some IntentResult {
        await ConversationSession.shared.startVoiceSession(source: .controlCenter)
        return .result()
    }
}
#endif
