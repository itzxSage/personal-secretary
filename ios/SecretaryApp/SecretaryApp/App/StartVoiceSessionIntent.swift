#if os(iOS)
import AppIntents
import SecretaryClient

struct StartVoiceSessionIntent: AudioRecordingIntent {
    static let title: LocalizedStringResource = "Start Voice Session"
    static let description = IntentDescription("Starts a voice conversation with Secretary.")
    static let openAppWhenRun = true

    @MainActor
    func perform() async throws -> some IntentResult {
#if !SECRETARY_WIDGET_EXTENSION
        await ConversationSession.shared.startVoiceSession(source: .appShortcut)
#endif
        return .result()
    }
}

@available(iOS 18.0, *)
struct StartControlCenterVoiceSessionIntent: AudioRecordingIntent {
    static let title: LocalizedStringResource = "Start Voice Session"
    static let description = IntentDescription("Opens Secretary and starts a voice session.")
    static let openAppWhenRun = true

    @MainActor
    func perform() async throws -> some IntentResult {
#if !SECRETARY_WIDGET_EXTENSION
        await ConversationSession.shared.startVoiceSession(source: .controlCenter)
#endif
        return .result()
    }
}
#endif
