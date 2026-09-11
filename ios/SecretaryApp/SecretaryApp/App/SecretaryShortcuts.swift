#if os(iOS)
import AppIntents

/// Registers the voice-session action with Shortcuts and Siri. Back Tap and
/// Action Button use a user-configured Shortcut binding on eligible devices.
struct SecretaryShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: StartVoiceSessionIntent(),
            phrases: ["Start a voice session with \(.applicationName)"],
            shortTitle: "Start Voice Session",
            systemImageName: "waveform"
        )
    }
}
#endif
