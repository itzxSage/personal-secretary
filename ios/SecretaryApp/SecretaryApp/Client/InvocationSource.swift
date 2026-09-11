import Foundation

/// Every way a user can start a voice session. The raw value is stable and
/// used in deep links and diagnostics.
public enum InvocationSource: String, Sendable, Equatable, CaseIterable {
    case pushToTalk = "push_to_talk"
    case appShortcut = "app_shortcut"
    case backTap = "back_tap"
    case actionButton = "action_button"
    case controlCenter = "control_center"
    case lockScreen = "lock_screen"
    case notification = "notification"

    /// The system surfaces that can be bound on a physical iPhone. Back Tap
    /// and the Action Button have no direct API; they are bound in Settings to
    /// a Shortcut that runs the app's App Intent, so they share the
    /// `appShortcut` handler path at runtime.
    public static let systemSurfaces: [InvocationSource] = [
        .pushToTalk,
        .appShortcut,
        .backTap,
        .actionButton,
        .controlCenter,
        .lockScreen,
    ]
}