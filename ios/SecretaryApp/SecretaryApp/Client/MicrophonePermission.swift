import AVFoundation

/// The microphone authorization states the invocation path understands.
public enum MicrophoneAuthorization: Sendable, Equatable {
    case granted
    case denied
    case undetermined
}

/// Abstraction over microphone permission so tests can stub every outcome
/// without touching the system prompt.
public protocol MicrophonePermissionProviding: Sendable {
    func requestAuthorization() async -> MicrophoneAuthorization
}

/// Real system-backed permission provider. The system prompt is only shown
/// when the user first triggers a voice surface; denial degrades to the text
/// conversation fallback instead of failing the app.
public struct MicrophoneAuthorizationProvider: MicrophonePermissionProviding {
    public init() {}

    public func requestAuthorization() async -> MicrophoneAuthorization {
        await AVCaptureDevice.requestAccess(for: .audio) ? .granted : .denied
    }
}